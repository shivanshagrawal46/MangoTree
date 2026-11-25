"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ThemeProvider } from "twenty-ui/theme";
import { EvidenceProvider } from "@/components/evidence";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

const UserCtx = React.createContext<{ user: User | null; loading: boolean; refresh: () => void }>({ user: null, loading: true, refresh: () => {} });
export const useUser = () => React.useContext(UserCtx);

function UserProvider({ children }: { children: React.ReactNode }) {
  const q = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me"), retry: false, staleTime: 60_000 });
  return <UserCtx.Provider value={{ user: q.data ?? null, loading: q.isLoading, refresh: () => q.refetch() }}>{children}</UserCtx.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  // Atlas is ~350ms per round trip from here; keep what we have for a minute and
  // show the previous page's data while the next loads, rather than a spinner.
  const [client] = React.useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, gcTime: 10 * 60_000, refetchOnWindowFocus: false, retry: 1 } } }));
  React.useEffect(() => {
    const dark = localStorage.getItem("mt-theme") === "dark";
    document.documentElement.classList.toggle("dark", dark);
  }, []);
  // twenty-ui takes its colours from the same light / dark switch as the rest of the app (the class on <html>).
  const dark = React.useSyncExternalStore(watchTheme, () => document.documentElement.classList.contains("dark"), () => false);
  return (
    <ThemeProvider colorScheme={dark ? "dark" : "light"}>
      <QueryClientProvider client={client}>
        <UserProvider>
          <EvidenceProvider>
            {children}
            <Toaster position="bottom-right" richColors closeButton toastOptions={{ style: { borderRadius: 8 } }} />
          </EvidenceProvider>
        </UserProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

function watchTheme(changed: () => void) {
  const seen = new MutationObserver(changed);
  seen.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => seen.disconnect();
}
