import { STATUS_LABEL, type TicketStatus } from "@/lib/api";

const STYLES: Record<TicketStatus, string> = {
  pending: "bg-amber-100 text-amber-800 ring-amber-300",
  resolved: "bg-emerald-100 text-emerald-800 ring-emerald-300",
  transferred_out: "bg-slate-200 text-slate-700 ring-slate-300",
};

export default function StatusBadge({ status }: { status: TicketStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${STYLES[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
