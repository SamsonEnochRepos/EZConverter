// Expo's default config already understands pnpm/monorepo symlinks (workspace packages are plain TS sources).
const { getDefaultConfig } = require("expo/metro-config");
module.exports = getDefaultConfig(__dirname);
