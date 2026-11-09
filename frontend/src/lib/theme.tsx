import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "vpn-panel-theme";

interface ThemeContextValue {
  theme: ThemeChoice;
  resolved: "light" | "dark";
  setTheme: (theme: ThemeChoice) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function resolveTheme(choice: ThemeChoice): "light" | "dark" {
  if (choice === "system") {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return choice;
}

function applyTheme(choice: ThemeChoice): "light" | "dark" {
  const resolved = resolveTheme(choice);
  document.documentElement.classList.toggle("dark", resolved === "dark");
  document.documentElement.style.colorScheme = resolved;
  return resolved;
}

export function readStoredTheme(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // localStorage может быть недоступен
  }
  return "system";
}

/** Применить тему до первого рендера (вызывается из main.tsx). */
export function applyInitialTheme(): void {
  applyTheme(readStoredTheme());
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(() => readStoredTheme());
  const [resolved, setResolved] = useState<"light" | "dark">(() => resolveTheme(readStoredTheme()));

  useEffect(() => {
    setResolved(applyTheme(theme));
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // игнорируем
    }
  }, [theme]);

  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => setResolved(applyTheme("system"));
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [theme]);

  const setTheme = useCallback((next: ThemeChoice) => setThemeState(next), []);
  const toggleTheme = useCallback(
    () => setThemeState((current) => (resolveTheme(current) === "dark" ? "light" : "dark")),
    [],
  );

  const value = useMemo(
    () => ({ theme, resolved, setTheme, toggleTheme }),
    [theme, resolved, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme должен использоваться внутри ThemeProvider");
  return context;
}
