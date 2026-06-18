import { forwardRef, type InputHTMLAttributes } from "react";

import { cn } from "../../lib/utils";

type SliderProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  label?: string;
  valueLabel?: string;
};

export const Slider = forwardRef<HTMLInputElement, SliderProps>(function Slider(
  { className, label, valueLabel, id, ...props },
  ref,
) {
  return (
    <div className="space-y-1">
      {(label || valueLabel) && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          {label && <label htmlFor={id}>{label}</label>}
          {valueLabel && <span>{valueLabel}</span>}
        </div>
      )}
      <input
        ref={ref}
        id={id}
        type="range"
        className={cn("h-2 w-full cursor-pointer accent-primary", className)}
        {...props}
      />
    </div>
  );
});
