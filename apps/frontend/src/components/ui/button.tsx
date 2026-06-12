import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Button — W13 AIS adoption (pill shape) on top of the W11-A polish.
 *
 *   - `rounded-full` pills are the Google AI Studio button shape. This is
 *     deliberately NOT driven by the --radius token — raising the token to
 *     a pill value would full-round cards/dialogs via the tailwind.config
 *     calc() derivations. Inputs keep the token radius on purpose.
 *   - `transition-all duration-fast ease-out-soft` — 150 ms eased hover /
 *     focus transition (W11 Linear polish, retained).
 *   - Focus ring `ring-2` + `ring-offset-2` (matches --ring, now the blue
 *     primary, for a coherent focus signal).
 *   - shadow-sm on default/destructive/outline resolves to a zero-alpha
 *     no-op under W13 tokens (AIS keeps in-flow surfaces flat); kept in
 *     the class lists so a future token change re-enables elevation
 *     without touching this file.
 *
 * The hex colors themselves are NOT in this file — they flow from the
 * tokens declared in `src/index.css` (W13).
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-medium ring-offset-background transition-all duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground shadow-sm hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline:
          "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 px-3 text-xs",
        lg: "h-10 px-6",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
