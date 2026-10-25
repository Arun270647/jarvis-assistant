import { cn } from "@/lib/utils";

/* Aceternity UI's Bento Grid (ui.aceternity.com/components/bento-grid), restyled with the app's HUD frame and
   tokens: square corners, hairline borders, the cyan accent on hover. */
export function BentoGrid({ className, children }: { className?: string; children?: React.ReactNode }) {
  return <div className={cn("grid grid-cols-1 gap-4 md:auto-rows-[minmax(17rem,auto)] md:grid-cols-2 lg:grid-cols-4", className)}>{children}</div>;
}

export function BentoGridItem({
  className, title, description, header, icon,
}: {
  className?: string;
  title: string;
  description: React.ReactNode;
  header?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <article
      className={cn(
        "group/bento hud-frame card relative flex flex-col justify-between gap-5 overflow-hidden p-6 transition-colors duration-300 hover:border-accent/40",
        className,
      )}
    >
      {header}
      <div className="transition-transform duration-300 group-hover/bento:translate-x-1">
        {icon ? <div className="text-accent">{icon}</div> : null}
        <h3 className="mt-3 text-[1.6rem]">{title}</h3>
        <div className="mt-2 text-[0.97rem] leading-relaxed text-muted">{description}</div>
      </div>
    </article>
  );
}
