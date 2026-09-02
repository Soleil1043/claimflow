import { STATUS_LABEL, type TicketStatus } from "@/lib/api";

/** 工单状态徽章（T057 / D030）：四态语义色 pill（cf-pill 工具类）。 */
const STYLES: Record<TicketStatus, string> = {
  pending: "warn",
  resolved: "ok",
  transferred_out: "muted",
};

export default function StatusBadge({ status }: { status: TicketStatus }) {
  return <span className={`cf-pill ${STYLES[status]}`}>{STATUS_LABEL[status]}</span>;
}
