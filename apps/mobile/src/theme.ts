import { Platform, useColorScheme } from "react-native";

export interface Theme {
  dark: boolean;
  bg: string; surface: string; surface2: string; text: string; muted: string; line: string; accent: string; accentText: string; accentSoft: string;
  good: string; goodSoft: string; warn: string; warnSoft: string; bad: string; badSoft: string; info: string; infoSoft: string;
  hero: string; heroText: string; heroSoft: string; shadow: string;
}
const light: Theme = { dark: false, bg: "#f1f5fc", surface: "#ffffff", surface2: "#f2f5fb", text: "#0b1530", muted: "#56627f", line: "#e1e7f2", accent: "#0b57e3", accentText: "#ffffff", accentSoft: "#e6eeff",
  good: "#0f7a3d", goodSoft: "#ddf6e6", warn: "#8a4b05", warnSoft: "#fff1d6", bad: "#b42318", badSoft: "#fee4e2", info: "#0b57e3", infoSoft: "#e6eeff",
  hero: "#0b57e3", heroText: "#ffffff", heroSoft: "rgba(255,255,255,0.18)", shadow: "#0b57e3" };
const dark: Theme = { dark: true, bg: "#060d1f", surface: "#0f1a33", surface2: "#16233f", text: "#e8eefb", muted: "#9db0d3", line: "#22325a", accent: "#6b97ff", accentText: "#06112b", accentSoft: "#14285a",
  good: "#6ee7a0", goodSoft: "#12301f", warn: "#fbbf6a", warnSoft: "#382a10", bad: "#fca5a5", badSoft: "#3b1618", info: "#93b4fb", infoSoft: "#14285a",
  hero: "#0e49c9", heroText: "#ffffff", heroSoft: "rgba(255,255,255,0.16)", shadow: "#000000" };

export function useTheme(pref: "system" | "light" | "dark" = "system"): Theme {
  const sys = useColorScheme();
  return (pref === "system" ? sys : pref) === "dark" ? dark : light;
}

/** Italic serif used for a single accent word inside a headline. */
export const serifFamily = (): string => (Platform.OS === "ios" ? "Georgia" : Platform.OS === "android" ? "serif" : "Georgia, 'Times New Roman', serif");
