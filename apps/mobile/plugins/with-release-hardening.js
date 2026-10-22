// Expo config plugin applied on `expo prebuild`:
//  1. signs the release APK with the keystore named by EZ_KEYSTORE_PATH / EZ_KEYSTORE_PASSWORD / EZ_KEY_ALIAS / EZ_KEY_PASSWORD
//     (the keystore lives outside the repository; without these variables the release build keeps the debug key and must not be published);
//  2. allows cleartext HTTP only to loopback (adb reverse / local testing). Every other host must use HTTPS;
//  3. on Windows, EZ_CXX_STAGING_DIR (a short path such as C:/x) moves the CMake build tree there: ninja embeds absolute source
//     paths in object paths, and the generated React Native codegen paths otherwise exceed Windows' 260-character limit.
const fs = require("node:fs");
const path = require("node:path");
const { withAppBuildGradle, withAndroidManifest, withDangerousMod } = require("expo/config-plugins");

const NETWORK_SECURITY_CONFIG = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="false" />
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="false">localhost</domain>
    <domain includeSubdomains="false">127.0.0.1</domain>
    <domain includeSubdomains="false">10.0.2.2</domain>
  </domain-config>
</network-security-config>
`;

const SIGNING_BLOCK = `release {
            if (System.getenv("EZ_KEYSTORE_PATH")) {
                storeFile file(System.getenv("EZ_KEYSTORE_PATH"))
                storePassword System.getenv("EZ_KEYSTORE_PASSWORD")
                keyAlias System.getenv("EZ_KEY_ALIAS")
                keyPassword System.getenv("EZ_KEY_PASSWORD")
            }
        }
        `;

const CXX_STAGING_BLOCK = `ndkVersion rootProject.ext.ndkVersion

    if (System.getenv("EZ_CXX_STAGING_DIR")) {
        externalNativeBuild {
            cmake {
                buildStagingDirectory = file(System.getenv("EZ_CXX_STAGING_DIR"))
            }
        }
    }`;

module.exports = function withReleaseHardening(config) {
  config = withAppBuildGradle(config, (cfg) => {
    let g = cfg.modResults.contents;
    if (g.includes("EZ_KEYSTORE_PATH")) return cfg;
    g = g.replace("ndkVersion rootProject.ext.ndkVersion", () => CXX_STAGING_BLOCK);
    g = g.replace(/signingConfigs \{\s*\n(\s*)debug \{/, (m, indent) => m.replace(/debug \{/, `${SIGNING_BLOCK}debug {`));
    g = g.replace(
      /(buildTypes \{[\s\S]*?release \{[\s\S]*?)signingConfig signingConfigs\.debug/,
      '$1signingConfig System.getenv("EZ_KEYSTORE_PATH") ? signingConfigs.release : signingConfigs.debug',
    );
    cfg.modResults.contents = g;
    return cfg;
  });
  config = withAndroidManifest(config, (cfg) => {
    cfg.modResults.manifest.application[0].$["android:networkSecurityConfig"] = "@xml/network_security_config";
    return cfg;
  });
  return withDangerousMod(config, [
    "android",
    (cfg) => {
      const dir = path.join(cfg.modRequest.platformProjectRoot, "app", "src", "main", "res", "xml");
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, "network_security_config.xml"), NETWORK_SECURITY_CONFIG);
      return cfg;
    },
  ]);
};
