import fnmatch
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen


root = Path(__file__).resolve().parents[1]
mods_root = root / "mods"
allowed_download_types = {"direct", "browser", "ad"}
allowed_direct_extensions = {".levipack", ".zip", ".so"}
id_pattern = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
sha256_pattern = re.compile(r"[0-9a-fA-F]{64}")
github_api = "https://api.github.com"
github_token = os.environ.get("GITHUB_TOKEN", "").strip()
fallback_url = os.environ.get("CATALOG_FALLBACK_URL", "").strip()
hub_raw_base = os.environ.get(
    "HUB_RAW_BASE",
    "https://raw.githubusercontent.com/QYCottage/LeviModHub/main",
).rstrip("/")


class ResourceNotFound(RuntimeError):
    pass


def require_text(data, key, source):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {key} is required")
    return value.strip()


def require_https(value, field, source):
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{source}: {field} must be an HTTPS URL")
    return value


def request(url, accept="application/vnd.github+json", use_token=False):
    headers = {
        "Accept": accept,
        "User-Agent": "LeviModHub-catalog-builder",
    }
    if use_token and github_token:
        headers["Authorization"] = f"Bearer {github_token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            raise ResourceNotFound(url) from error
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} for {url}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Could not fetch {url}: {error.reason}") from error


def request_json(url, use_token=False):
    return json.loads(request(url, use_token=use_token).decode("utf-8"))


def request_text(url):
    return request(url, accept="text/plain").decode("utf-8")


def load_previous_catalog():
    if not fallback_url:
        return {}
    try:
        data = request_json(fallback_url)
    except Exception as error:
        print(f"Warning: could not load previous catalog: {error}")
        return {}
    mods = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(mods, list):
        return {}
    return {
        mod.get("id"): mod
        for mod in mods
        if isinstance(mod, dict) and isinstance(mod.get("id"), str)
    }


def normalize_tags(value, source):
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise ValueError(f"{source}: tags must be a list of text values")
    return [item.strip() for item in value]


def normalize_minecraft_versions(value, source):
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"{source}: minecraft_versions must contain at least one version"
        )
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(
            f"{source}: minecraft_versions must contain text values"
        )
    return [item.strip() for item in value]


def direct_extension(name):
    return Path(name).suffix.lower()


def direct_asset_name(url):
    name = Path(urlparse(url).path).name
    return name or "download"


def load_static_mod(data, source):
    for key in ("id", "name", "author", "description"):
        data[key] = require_text(data, key, source)

    icon_url = data.get("icon_url")
    local_icon = source.parent / "icon.png"
    if isinstance(icon_url, str) and icon_url.strip():
        data["icon_url"] = require_https(
            icon_url.strip(), "icon_url", source
        )
    elif local_icon.is_file():
        data["icon_url"] = (
            f"{hub_raw_base}/mods/{data['id']}/icon.png"
        )
    else:
        data.pop("icon_url", None)

    homepage_url = data.get("homepage_url", "")
    if homepage_url:
        data["homepage_url"] = require_https(
            homepage_url, "homepage_url", source
        )

    data["tags"] = normalize_tags(data.get("tags", []), source)

    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError(f"{source}: add at least one release")

    normalized_releases = []
    for release in releases:
        if not isinstance(release, dict):
            raise ValueError(
                f"{source}: releases must contain objects"
            )

        version = require_text(release, "version", source)
        minecraft_versions = normalize_minecraft_versions(
            release.get("minecraft_versions"), source
        )
        download_type = require_text(
            release, "download_type", source
        ).lower()

        if download_type not in allowed_download_types:
            raise ValueError(
                f"{source}: download_type must be direct, browser, or ad"
            )

        download_url = require_https(
            require_text(release, "download_url", source),
            "download_url",
            source,
        )
        published_at = require_text(
            release, "published_at", source
        )

        normalized = {
            "version": version,
            "minecraft_versions": minecraft_versions,
            "download_type": download_type,
            "published_at": published_at,
        }

        if download_type == "direct":
            name = direct_asset_name(download_url)
            if direct_extension(name) not in allowed_direct_extensions:
                raise ValueError(
                    f"{source}: direct downloads must end in "
                    ".levipack, .zip, or .so"
                )
            normalized["assets"] = [
                {
                    "name": name,
                    "download_url": download_url,
                }
            ]
        else:
            normalized["download_url"] = download_url

        normalized_releases.append(normalized)

    normalized_releases.sort(
        key=lambda release: release["published_at"],
        reverse=True,
    )
    data["releases"] = normalized_releases
    data.pop("provider", None)
    return data


def github_release_url(repository, per_page):
    return (
        f"{github_api}/repos/{repository}/releases"
        f"?per_page={per_page}"
    )


def raw_url(repository, ref, path):
    return (
        f"https://raw.githubusercontent.com/{repository}/"
        f"{quote(ref, safe='')}/"
        f"{quote(path.lstrip('/'), safe='/')}"
    )


def version_from_tag(tag, prefix):
    if not tag.startswith(prefix):
        return None
    version = tag[len(prefix):].strip()
    return version or None


def load_manifest(repository, metadata_path, ref):
    source = f"{repository}@{ref}/{metadata_path}"
    data = json.loads(
        request_text(raw_url(repository, ref, metadata_path))
    )
    if not isinstance(data, dict):
        raise ValueError(
            f"{source}: manifest must be a JSON object"
        )
    if data.get("schema_version") != 1:
        raise ValueError(
            f"{source}: unsupported schema_version"
        )
    return data, source


def normalize_asset_rules(manifest, source):
    rules = manifest.get("release_assets", {})
    if rules is None:
        rules = {}
    if not isinstance(rules, dict):
        raise ValueError(
            f"{source}: release_assets must be an object"
        )

    include = rules.get(
        "include", ["*.levipack", "*.so", "*.zip"]
    )
    exclude = rules.get("exclude", [])
    labels = rules.get("labels", {})

    if not isinstance(include, list) or not include or any(
        not isinstance(item, str) or not item
        for item in include
    ):
        raise ValueError(
            f"{source}: release_assets.include must contain patterns"
        )

    if not isinstance(exclude, list) or any(
        not isinstance(item, str) or not item
        for item in exclude
    ):
        raise ValueError(
            f"{source}: release_assets.exclude must contain patterns"
        )

    if not isinstance(labels, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or not value.strip()
        for key, value in labels.items()
    ):
        raise ValueError(
            f"{source}: release_assets.labels must map file names "
            "to labels"
        )

    return (
        include,
        exclude,
        {key: value.strip() for key, value in labels.items()},
    )


def matches_any(name, patterns):
    lower_name = name.lower()
    return any(
        fnmatch.fnmatchcase(lower_name, pattern.lower())
        for pattern in patterns
    )


def normalize_digest(value):
    if not isinstance(value, str) or not value:
        return None
    digest = (
        value.split(":", 1)[1]
        if value.lower().startswith("sha256:")
        else value
    )
    return (
        digest.lower()
        if sha256_pattern.fullmatch(digest)
        else None
    )


def collect_release_assets(
    repository,
    release,
    manifest,
    source,
):
    include, exclude, labels = normalize_asset_rules(
        manifest, source
    )
    assets = release.get("assets")
    if not isinstance(assets, list):
        return []

    result = []
    expected_prefix = (
        f"/{repository}/releases/download/".lower()
    )

    for asset in assets:
        if not isinstance(asset, dict):
            continue

        name = asset.get("name")
        url = asset.get("browser_download_url")

        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(url, str)
        ):
            continue

        name = name.strip()

        if direct_extension(name) not in allowed_direct_extensions:
            continue
        if (
            not matches_any(name, include)
            or matches_any(name, exclude)
        ):
            continue

        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
        ):
            continue
        if not parsed.path.lower().startswith(expected_prefix):
            continue

        entry = {
            "name": name,
            "download_url": url,
        }

        label = labels.get(name)
        if label:
            entry["label"] = label

        size = asset.get("size")
        if isinstance(size, int) and size >= 0:
            entry["size"] = size

        digest = normalize_digest(asset.get("digest"))
        if digest:
            entry["sha256"] = digest

        result.append(entry)

    return result


def mod_info_from_manifest(
    registration,
    manifest,
    source,
    repository,
    ref,
):
    manifest_id = require_text(manifest, "id", source)
    if manifest_id != registration["id"]:
        raise ValueError(
            f"{source}: id must be {registration['id']}"
        )

    info = manifest.get("info")
    if not isinstance(info, dict):
        raise ValueError(f"{source}: info is required")

    name = require_text(info, "name", source)
    author = require_text(info, "author", source)
    description = require_text(
        info, "description", source
    )

    homepage_url = info.get(
        "homepage_url",
        f"https://github.com/{repository}",
    )
    if homepage_url:
        homepage_url = require_https(
            homepage_url, "homepage_url", source
        )

    icon_url = ""
    explicit_icon = info.get("icon_url")
    icon_path = info.get("icon")

    if isinstance(explicit_icon, str) and explicit_icon.strip():
        icon_url = require_https(
            explicit_icon.strip(), "icon_url", source
        )
    elif isinstance(icon_path, str) and icon_path.strip():
        icon_url = raw_url(
            repository, ref, icon_path.strip()
        )

    result = {
        "id": registration["id"],
        "name": name,
        "author": author,
        "description": description,
        "homepage_url": homepage_url,
        "tags": normalize_tags(
            info.get("tags", []), source
        ),
    }

    if icon_url:
        result["icon_url"] = icon_url

    return result


def load_github_mod(registration, source):
    repository = require_text(
        registration, "repository", source
    )
    if not repo_pattern.fullmatch(repository):
        raise ValueError(
            f"{source}: repository must be owner/name"
        )

    metadata_path = registration.get(
        "metadata_path", "levimod.json"
    )
    if (
        not isinstance(metadata_path, str)
        or not metadata_path.strip()
        or metadata_path.startswith("/")
        or ".." in Path(metadata_path).parts
    ):
        raise ValueError(
            f"{source}: metadata_path must be a "
            "repository-relative path"
        )
    metadata_path = metadata_path.strip()

    tag_prefix = registration.get("tag_prefix", "v")
    if not isinstance(tag_prefix, str):
        raise ValueError(
            f"{source}: tag_prefix must be text"
        )

    include_prereleases = registration.get(
        "include_prereleases", False
    )
    if not isinstance(include_prereleases, bool):
        raise ValueError(
            f"{source}: include_prereleases must be true or false"
        )

    bootstrap_ref = registration.get(
        "bootstrap_ref", ""
    )
    if bootstrap_ref is None:
        bootstrap_ref = ""
    if not isinstance(bootstrap_ref, str):
        raise ValueError(
            f"{source}: bootstrap_ref must be text"
        )

    max_releases = registration.get("max_releases", 20)
    if (
        not isinstance(max_releases, int)
        or max_releases < 1
        or max_releases > 100
    ):
        raise ValueError(
            f"{source}: max_releases must be between 1 and 100"
        )

    releases = request_json(
        github_release_url(repository, max_releases),
        use_token=True,
    )
    if not isinstance(releases, list):
        raise ValueError(
            f"{source}: GitHub releases response was invalid"
        )

    catalog_releases = []
    catalog_mod = None
    considered_releases = 0

    for release in releases:
        if (
            not isinstance(release, dict)
            or release.get("draft") is True
        ):
            continue

        if (
            release.get("prerelease") is True
            and not include_prereleases
        ):
            continue

        tag = release.get("tag_name")
        if not isinstance(tag, str):
            continue

        expected_version = version_from_tag(
            tag, tag_prefix
        )
        if not expected_version:
            continue

        newest_candidate = considered_releases == 0
        considered_releases += 1
        manifest_ref = tag

        try:
            manifest, manifest_source = load_manifest(
                repository,
                metadata_path,
                manifest_ref,
            )
        except ResourceNotFound:
            if not newest_candidate or not bootstrap_ref:
                print(
                    f"Warning: {repository} {tag} has no "
                    f"{metadata_path}; skipping release"
                )
                continue

            manifest_ref = bootstrap_ref
            try:
                manifest, manifest_source = load_manifest(
                    repository,
                    metadata_path,
                    manifest_ref,
                )
            except ResourceNotFound:
                print(
                    f"Warning: {repository} has no "
                    f"{metadata_path} at {tag} or "
                    f"{bootstrap_ref}; skipping release"
                )
                continue

        manifest_version = require_text(
            manifest, "version", manifest_source
        )

        if manifest_version != expected_version:
            print(
                f"Warning: {manifest_source}: version "
                f"{manifest_version} does not match tag "
                f"{tag}; skipping release"
            )
            continue

        minecraft_versions = normalize_minecraft_versions(
            manifest.get("minecraft_versions"),
            manifest_source,
        )

        assets = collect_release_assets(
            repository,
            release,
            manifest,
            manifest_source,
        )
        if not assets:
            print(
                f"Warning: {repository} {tag} has no "
                "supported release assets; skipping release"
            )
            continue

        published_at = (
            release.get("published_at")
            or release.get("created_at")
        )
        if (
            not isinstance(published_at, str)
            or not published_at.strip()
        ):
            print(
                f"Warning: {repository} {tag} has no "
                "published timestamp; skipping release"
            )
            continue

        release_entry = {
            "version": manifest_version,
            "minecraft_versions": minecraft_versions,
            "download_type": "direct",
            "assets": assets,
            "published_at": published_at.strip(),
        }
        catalog_releases.append(release_entry)

        if catalog_mod is None:
            catalog_mod = mod_info_from_manifest(
                registration,
                manifest,
                manifest_source,
                repository,
                manifest_ref,
            )

    if catalog_mod is None or not catalog_releases:
        raise ValueError(
            f"{source}: no valid GitHub releases were discovered"
        )

    catalog_releases.sort(
        key=lambda release: release["published_at"],
        reverse=True,
    )
    catalog_mod["releases"] = catalog_releases
    return catalog_mod


def load_url_manifest(metadata_url):
    data = json.loads(request_text(metadata_url))
    if not isinstance(data, dict):
        raise ValueError(
            f"{metadata_url}: manifest must be a JSON object"
        )
    if data.get("schema_version") != 1:
        raise ValueError(
            f"{metadata_url}: unsupported schema_version"
        )
    return data


def mod_info_from_url_manifest(
    registration,
    manifest,
    metadata_url,
    registration_source,
):
    manifest_id = require_text(
        manifest, "id", metadata_url
    )
    if manifest_id != registration["id"]:
        raise ValueError(
            f"{metadata_url}: id must be "
            f"{registration['id']}"
        )

    info = manifest.get("info")
    if not isinstance(info, dict):
        raise ValueError(
            f"{metadata_url}: info is required"
        )

    result = {
        "id": registration["id"],
        "name": require_text(
            info, "name", metadata_url
        ),
        "author": require_text(
            info, "author", metadata_url
        ),
        "description": require_text(
            info, "description", metadata_url
        ),
        "tags": normalize_tags(
            info.get("tags", []), metadata_url
        ),
    }

    homepage_url = info.get("homepage_url", "")
    if homepage_url:
        result["homepage_url"] = require_https(
            homepage_url,
            "homepage_url",
            metadata_url,
        )
    else:
        result["homepage_url"] = ""

    explicit_icon = info.get("icon_url")
    icon_path = info.get("icon")
    local_icon = registration_source.parent / "icon.png"

    if isinstance(explicit_icon, str) and explicit_icon.strip():
        result["icon_url"] = require_https(
            explicit_icon.strip(),
            "icon_url",
            metadata_url,
        )
    elif isinstance(icon_path, str) and icon_path.strip():
        resolved_icon = urljoin(metadata_url, icon_path.strip())
        result["icon_url"] = require_https(
            resolved_icon,
            "icon",
            metadata_url,
        )
    elif local_icon.is_file():
        result["icon_url"] = (
            f"{hub_raw_base}/mods/"
            f"{registration['id']}/icon.png"
        )

    return result


def load_url_mod(
    registration,
    source,
    previous_mod,
):
    metadata_url = require_https(
        require_text(
            registration, "metadata_url", source
        ),
        "metadata_url",
        source,
    )

    max_releases = registration.get("max_releases", 20)
    if (
        not isinstance(max_releases, int)
        or max_releases < 1
        or max_releases > 100
    ):
        raise ValueError(
            f"{source}: max_releases must be between 1 and 100"
        )

    manifest = load_url_manifest(metadata_url)

    version = require_text(
        manifest, "version", metadata_url
    )
    minecraft_versions = normalize_minecraft_versions(
        manifest.get("minecraft_versions"),
        metadata_url,
    )
    published_at = require_text(
        manifest, "published_at", metadata_url
    )

    download = manifest.get("download")
    if not isinstance(download, dict):
        raise ValueError(
            f"{metadata_url}: download is required"
        )

    download_type = require_text(
        download, "type", metadata_url
    ).lower()
    if download_type not in allowed_download_types:
        raise ValueError(
            f"{metadata_url}: download.type must be "
            "direct, browser, or ad"
        )

    download_url = require_https(
        require_text(download, "url", metadata_url),
        "download.url",
        metadata_url,
    )

    current_release = {
        "version": version,
        "minecraft_versions": minecraft_versions,
        "download_type": download_type,
        "published_at": published_at,
    }

    if download_type == "direct":
        name = download.get("name")
        if isinstance(name, str) and name.strip():
            name = name.strip()
        else:
            name = direct_asset_name(download_url)

        if direct_extension(name) not in allowed_direct_extensions:
            raise ValueError(
                f"{metadata_url}: direct download name must "
                "end in .levipack, .zip, or .so"
            )

        asset = {
            "name": name,
            "download_url": download_url,
        }

        label = download.get("label")
        if isinstance(label, str) and label.strip():
            asset["label"] = label.strip()

        size = download.get("size")
        if isinstance(size, int) and size >= 0:
            asset["size"] = size

        digest = normalize_digest(
            download.get("sha256")
        )
        if digest:
            asset["sha256"] = digest

        current_release["assets"] = [asset]
    else:
        current_release["download_url"] = download_url

    catalog_mod = mod_info_from_url_manifest(
        registration,
        manifest,
        metadata_url,
        source,
    )

    releases = [current_release]

    if isinstance(previous_mod, dict):
        previous_releases = previous_mod.get("releases")
        if isinstance(previous_releases, list):
            for release in previous_releases:
                if not isinstance(release, dict):
                    continue
                if release.get("version") == version:
                    continue
                releases.append(release)

    releases.sort(
        key=lambda release: str(
            release.get("published_at", "")
        ),
        reverse=True,
    )

    catalog_mod["releases"] = releases[:max_releases]
    return catalog_mod


def load_registration(source, previous_mods):
    data = json.loads(
        source.read_text(encoding="utf-8")
    )
    if not isinstance(data, dict):
        raise ValueError(
            f"{source}: mod.json must be an object"
        )

    mod_id = require_text(data, "id", source)
    if not id_pattern.fullmatch(mod_id):
        raise ValueError(f"{source}: invalid id")
    if mod_id != source.parent.name:
        raise ValueError(
            f"{source}: id must match its folder name"
        )

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(
            f"{source}: enabled must be true or false"
        )
    if not enabled:
        return None

    provider = data.get("provider", "static")
    if not isinstance(provider, str):
        raise ValueError(
            f"{source}: provider must be text"
        )
    provider = provider.strip().lower()

    if provider == "static":
        return load_static_mod(data, source)

    previous = previous_mods.get(mod_id)

    try:
        if provider == "github":
            return load_github_mod(data, source)
        if provider == "url":
            return load_url_mod(
                data, source, previous
            )
        raise ValueError(
            f"{source}: provider must be static, github, or url"
        )
    except Exception as error:
        if previous is None:
            raise
        print(
            f"Warning: {source}: {error}; "
            "keeping previous published entry"
        )
        return previous


def main():
    previous_mods = load_previous_catalog()
    mods = []

    for source in sorted(
        mods_root.glob("*/mod.json")
    ):
        mod = load_registration(
            source, previous_mods
        )
        if mod is not None:
            mods.append(mod)

    ids = [mod["id"] for mod in mods]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate mod id")

    mods.sort(
        key=lambda mod: mod["name"].casefold()
    )

    output = root / "dist"
    output.mkdir(exist_ok=True)

    (output / "catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "mods": mods,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Generated catalog with {len(mods)} mods"
    )


if __name__ == "__main__":
    main()
