import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { z } from "zod";

import { AuthLayout } from "@/pages/auth/AuthLayout";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

function buildSchema(t: (key: string) => string) {
  return z.object({
    email: z.string().email({ message: t("errors.email_invalid") }),
  });
}

type ForgotValues = z.infer<ReturnType<typeof buildSchema>>;

export function ForgotPasswordPage() {
  const { t } = useTranslation("auth");
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<ForgotValues>({
    resolver: zodResolver(buildSchema(t)),
    defaultValues: { email: "" },
  });

  function onSubmit(_values: ForgotValues) {
    // The backend /auth/forgot-password endpoint lands in PR #18.
    // For now we surface a stub success message so the UX is complete.
    setSubmitted(true);
  }

  return (
    <AuthLayout
      testId="forgot-page"
      title={t("forgot.title")}
      subtitle={t("forgot.subtitle")}
      footer={
        <Link
          to="/login"
          className="font-medium text-primary hover:underline"
          data-testid="forgot-back-link"
        >
          {t("forgot.back_to_login")}
        </Link>
      }
    >
      {submitted ? (
        <Alert data-testid="forgot-success">
          <CheckCircle2 className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("forgot.success")}</AlertDescription>
        </Alert>
      ) : null}

      <Form {...form}>
        <form
          noValidate
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-4"
          data-testid="forgot-form"
        >
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t("forgot.email_label")}</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    data-testid="forgot-email"
                    disabled={submitted}
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            className="w-full"
            disabled={submitted}
            data-testid="forgot-submit"
          >
            {t("forgot.submit")}
          </Button>
        </form>
      </Form>
    </AuthLayout>
  );
}
