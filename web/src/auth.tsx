import { createContext, useContext } from "react";
import type { User } from "./api";

export const AuthContext = createContext<{ user: User; signOut: () => void; refreshCounts: () => void } | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("Not signed in");
  return ctx;
}

export const INTERNAL = ["ops_analyst", "ops_supervisor", "aml_compliance", "sanctions", "periodic_review", "platform_admin"];
export const isInternal = (u: User) => INTERNAL.includes(u.role);
