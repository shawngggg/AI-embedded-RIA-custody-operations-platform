import type { MouseEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "./auth";

/** A link to a case for people whose role includes case files; plain text for everyone else (the platform admin). */
export function CaseLink({ id, children, title, onClick }: { id: number; children: ReactNode; title?: string; onClick?: (e: MouseEvent) => void }) {
  const { user } = useAuth();
  return user.capabilities?.cases ? <Link to={`/cases/${id}`} title={title} onClick={onClick}>{children}</Link> : <span title={title}>{children}</span>;
}
