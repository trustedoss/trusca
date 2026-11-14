/**
 * The react-hook-form glue for Primer inputs.
 *
 * Each screen that moves off the shadcn form helpers relies on three things:
 * the label reaches the input, an invalid field is marked and described, and
 * Primer's own red border is switched on.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TextInput } from "@primer/react";
import { useForm } from "react-hook-form";
import { describe, expect, it } from "vitest";

import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/primer/form";

function Sample() {
  const form = useForm<{ email: string }>({ defaultValues: { email: "" } });
  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(() => undefined)}
        noValidate
      >
        <FormField
          control={form.control}
          name="email"
          rules={{ required: "Email is required" }}
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <TextInput block data-testid="email" {...field} />
              </FormControl>
              <FormDescription>We never share it.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <button type="submit">go</button>
      </form>
    </Form>
  );
}

describe("primer form glue", () => {
  it("connects the label to the input", () => {
    render(<Sample />);
    expect(screen.getByLabelText("Email")).toBe(screen.getByTestId("email"));
  });

  it("describes a valid field by its hint only", () => {
    render(<Sample />);
    const input = screen.getByTestId("email");
    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(input.getAttribute("aria-describedby")).not.toMatch(/message/);
  });

  it("marks an invalid field, points at the message, and turns the border red", async () => {
    render(<Sample />);
    await userEvent.setup().click(screen.getByRole("button", { name: "go" }));
    const input = screen.getByTestId("email");
    await waitFor(() => expect(input).toHaveAttribute("aria-invalid", "true"));
    const message = screen.getByText("Email is required");
    expect(input.getAttribute("aria-describedby")).toContain(message.id);
    // Primer's invalid style is a data attribute on the wrapper around the input.
    expect(input.closest("[data-validation-status], [class*='TextInput']")?.outerHTML).toMatch(
      /validation|error/i,
    );
  });
});
