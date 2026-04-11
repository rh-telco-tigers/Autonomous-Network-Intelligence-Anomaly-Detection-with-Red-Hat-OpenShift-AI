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
      <span className="select-none pl-[0.22em] text-[0.95rem] font-black uppercase leading-none tracking-[0.22em]">
        ANI
      </span>
    </div>
  );
}
