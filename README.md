# LeviModHub

LeviModHub is the external mod catalog used by LeviLauncher on Android.

Mods can still use the old manual catalog format. Mods can also use automatic updates through GitHub Releases or through a stable `levimod.json` URL.

## GitHub Releases

For mods that publish files through GitHub Releases, add a `levimod.json` file to the root of the mod repository.

A working example can be found here:

[QYCottage/BedrockTools - levimod.json](https://github.com/QYCottage/BedrockTools/blob/main/levimod.json)

Example:

```json
{
  "schema_version": 1,
  "id": "your-mod-id",
  "version": "1.0.0",
  "minecraft_versions": [
    "1.26.45.1"
  ],
  "info": {
    "name": "Your Mod",
    "author": "Your Name",
    "description": "A short description of your mod.",
    "homepage_url": "https://github.com/you/your-mod",
    "icon": "assets/icon.png",
    "tags": [
      "Utility"
    ]
  },
  "release_assets": {
    "include": [
      "*.levipack",
      "*.so"
    ],
    "labels": {
      "YourMod.levipack": "LeviPack",
      "libYourMod.so": "Native library"
    }
  }
}
```

Then add this to LeviModHub:

```text
mods/your-mod-id/mod.json
```

```json
{
  "id": "your-mod-id",
  "provider": "github",
  "repository": "you/your-mod",
  "metadata_path": "levimod.json",
  "include_prereleases": false,
  "max_releases": 20,
  "enabled": true
}
```

After the mod is accepted, future GitHub releases are picked up automatically.

## Custom download links

Mods that use MediaFire, a website, a CDN, or another download service can also update automatically.

The important part is that the `levimod.json` URL stays the same. The actual download link inside it can change every release.

A simple way to do this is to keep `levimod.json` in the mod's GitHub repository, even if the mod file itself is hosted somewhere else.

Example:

```json
{
  "schema_version": 1,
  "id": "your-mod-id",
  "version": "2.0.0",
  "minecraft_versions": [
    "1.26.45.1"
  ],
  "published_at": "2026-09-13T00:00:00Z",
  "info": {
    "name": "Your Mod",
    "author": "Your Name",
    "description": "A short description of your mod.",
    "homepage_url": "https://github.com/you/your-mod",
    "tags": [
      "Utility"
    ]
  },
  "download": {
    "type": "browser",
    "url": "https://example.com/your-new-download-link"
  }
}
```

Then add this to LeviModHub:

```json
{
  "id": "your-mod-id",
  "provider": "url",
  "metadata_url": "https://raw.githubusercontent.com/you/your-mod/main/levimod.json",
  "max_releases": 20,
  "enabled": true
}
```

For a page such as MediaFire, use:

```json
"type": "browser"
```

For a direct `.levipack`, `.so`, or `.zip` URL, use:

```json
"download": {
  "type": "direct",
  "url": "https://example.com/YourMod.levipack"
}
```

If a direct URL does not end with the real file name, add `name`:

```json
"download": {
  "type": "direct",
  "url": "https://example.com/download?id=123",
  "name": "YourMod.levipack"
}
```

When releasing a new version, only update the mod's own `levimod.json`:

- `version`
- `minecraft_versions`
- `published_at`
- `download.url`

LeviModHub checks the metadata automatically. No new LeviModHub pull request is needed for normal updates.

Previously published versions are kept from the existing catalog, up to `max_releases`.

## Minecraft versions

Use an exact Minecraft version:

```json
"minecraft_versions": [
  "1.26.45.1"
]
```

Multiple versions can be listed:

```json
"minecraft_versions": [
  "1.26.45.1",
  "1.26.50.2"
]
```

`X` can be used for a compatible range:

```json
"minecraft_versions": [
  "1.26.5X.X"
]
```

`>=` can be used when a mod supports one version and every newer version:

```json
"minecraft_versions": [
  ">=1.26.45.1"
]
```

## Existing mods

The old manual `mod.json` format is still supported.

Developers can move to either `provider: "github"` or `provider: "url"` when they are ready.
