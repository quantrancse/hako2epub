# hako2epub for Android

The Android build of [hako2epub](../README.md), packaged with
[BeeWare Briefcase](https://briefcase.readthedocs.io/). Same downloader as the
desktop tool, saving into `Downloads/hako2epub`.

End users do not need any of this — they can install the APK from the
[latest release](https://github.com/quantrancse/hako2epub/releases/latest).

## Building locally

Requires a JDK 17 (the Android Gradle Plugin in the Briefcase template needs
it). Briefcase downloads its own if none is found.

```sh
cd android
briefcase create android
briefcase build android          # debug APK, installable for testing
briefcase run android
```

`briefcase package android -p apk` produces an **unsigned** release APK that
Android refuses to install. It only becomes installable after the signing step
below, which is what CI does.

## Releasing

`.github/workflows/build-release.yml` builds and publishes both the Windows
executable and the signed Android APK when a `v*` tag is pushed.

The APK is signed with a release key you create once and keep. Android
identifies an app by its package name **plus its signing certificate**, so
every release must use the same key — otherwise users cannot update in place
and must uninstall first, losing their `ln_info.json`.

### 1. Create the keystore (once)

```sh
keytool -genkeypair -v \
  -keystore hako2epub-release.jks \
  -storetype PKCS12 \
  -alias hako2epub \
  -keyalg RSA -keysize 4096 \
  -validity 10000
```

PKCS12 keystores use one password for both the store and the key, so enter the
same value if prompted twice.

> **Keep this file safe, outside the repository, and never commit it.** If you
> lose it you can never publish an update to the same app again. `.gitignore`
> excludes `*.jks` and `*.keystore` as a backstop.

### 2. Add the secrets

```sh
base64 -i hako2epub-release.jks | pbcopy      # macOS
base64 -w0 hako2epub-release.jks              # Linux
```

Add these as secrets in the `hako2epub` environment
(**Settings → Environments → hako2epub → Environment secrets**):

| Secret | Value |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | the base64 text |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore password |
| `ANDROID_KEY_ALIAS` | `hako2epub` |
| `ANDROID_KEY_PASSWORD` | same as the keystore password (PKCS12) |

The `build-android` job declares `environment: hako2epub`, which is what makes
these visible to it. If you move them to repository secrets instead, drop that
line from the workflow. Any protection rule on the environment (required
reviewers, wait timer, or a deployment branch filter excluding tags) will pause
or block the release job.

### 3. Tag

```sh
git tag v2.4.0
git push origin v2.4.0
```

The workflow fails early with a clear message if the secrets are missing,
rather than publishing an APK nobody can install.

## Play Protect

The APK is sideloaded rather than distributed through Google Play, so Android
warns about installing from an unknown source and Play Protect may ask for
confirmation — **More details → Install anyway**. This is unrelated to signing:
it happens because the app is not distributed through Play and because it
requests *All files access*, a sensitive permission. Signing correctly does not
remove the warning.
