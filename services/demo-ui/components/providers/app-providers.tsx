"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";

type ApiTokenContextValue = {
  token: string;
  setToken: (value: string) => void;
};

const ApiTokenContext = React.createContext<ApiTokenContextValue | null>(null);
const apiTokenStorageKey = "ims-console-api-token";
const themeStorageKey = "ims-console-theme";

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  const [token, setTokenState] = React.useState("demo-token");

  React.useEffect(() => {
    const value = window.localStorage.getItem(apiTokenStorageKey)?.trim();
    if (value) {
      setTokenState(value);
    }
  }, []);

  React.useEffect(() => {
    const storedTheme = window.localStorage.getItem(themeStorageKey)?.trim();
    if (storedTheme === "light" || storedTheme === "dark") {
      return;
    }
    window.localStorage.setItem(themeStorageKey, "dark");
  }, []);

  const setToken = React.useCallback(
    (value: string) => {
      const next = value.trim() || "demo-token";
      window.localStorage.setItem(apiTokenStorageKey, next);
      setTokenState(next);
      queryClient.invalidateQueries();
    },
    [queryClient],
  );

  return (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange storageKey={themeStorageKey}>
      <ApiTokenContext.Provider value={{ token, setToken }}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </ApiTokenContext.Provider>
    </ThemeProvider>
  );
}

export function useApiToken() {
  const context = React.useContext(ApiTokenContext);
  if (!context) {
    throw new Error("useApiToken must be used within AppProviders");
  }
  return context;
}
