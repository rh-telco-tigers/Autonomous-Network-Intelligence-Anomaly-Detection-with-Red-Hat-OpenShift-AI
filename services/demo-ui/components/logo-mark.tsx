import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex items-center justify-center rounded-2xl bg-[radial-gradient(circle_at_top,_rgba(14,165,233,0.22),_transparent_58%),linear-gradient(180deg,_rgba(255,255,255,0.98),_rgba(224,242,254,0.92))] text-[var(--accent)] shadow-sm ring-1 ring-[var(--accent-ring)] dark:bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.22),_transparent_58%),linear-gradient(180deg,_rgba(12,22,40,0.98),_rgba(8,47,73,0.92))]",
        className,
      )}
    >
      <svg viewBox="0 0 48 48" className="h-7 w-7" fill="none">
        <path d="M7 36 16.5 12h4L11 36H7Z" fill="currentColor" opacity="0.92" />
        <path d="M15.5 28h12.5l-1.7 4H13.8l1.7-4Z" fill="currentColor" />
        <path d="M22 36 31.5 12h4L26 36h-4Z" fill="currentColor" opacity="0.92" />
        <path d="M31 12h4.2L41 25.8V12h3v24h-3.6L34 22.3V36h-3V12Z" fill="currentColor" />
      </svg>
    </div>
  );
}
