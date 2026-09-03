"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { Toaster } from "sonner";
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
  return (
    <QueryClientProvider client={client}>
      <UserProvider>
        <EvidenceProvider>
          {children}
          <Toaster position="bottom-right" richColors closeButton toastOptions={{ style: { borderRadius: 12 } }} />
        </EvidenceProvider>
      </UserProvider>
    </QueryClientProvider>
  );
}
