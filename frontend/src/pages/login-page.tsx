import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Eye, EyeOff, KeyRound, Loader2, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, isMockMode, setCsrfToken } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import { meQuery, queryKeys } from "@/lib/queries";
import { applyInitialTheme, useTheme } from "@/lib/theme";
import { Moon, Sun } from "lucide-react";

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const search = useSearch({ from: "/login" });
  const { resolved, toggleTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = useMutation({
    mutationFn: () => api.auth.login(email.trim(), password),
    onSuccess: async (data) => {
      setCsrfToken(data.csrf_token);
      queryClient.setQueryData(queryKeys.me, {
        email: data.user.email,
        csrf_token: data.csrf_token,
        weak_password: false,
      });
      await queryClient.invalidateQueries({ queryKey: meQuery.queryKey });
      const redirect = search.redirect && search.redirect.startsWith("/") ? search.redirect : "/dashboard";
      await navigate({ to: redirect });
    },
    onError: (mutationError) => setError(errorMessage(mutationError)),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!email.trim()) {
      setError("Введите email администратора");
      return;
    }
    if (!password) {
      setError("Введите пароль");
      return;
    }
    login.mutate();
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-slate-950 p-10 text-slate-100 lg:flex">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(99,102,241,0.35),transparent_45%),radial-gradient(circle_at_80%_70%,rgba(20,184,166,0.25),transparent_40%)]" />
        <div className="relative flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/20">
            <ShieldCheck className="size-6 text-cyan-300" aria-hidden />
          </div>
          <div>
            <p className="text-lg font-semibold">VPN Control Panel</p>
            <p className="text-sm text-slate-400">Xray · VLESS + REALITY</p>
          </div>
        </div>
        <div className="relative max-w-md space-y-6">
          <h1 className="text-3xl font-semibold leading-tight">
            Один администратор. Полный контроль над VPN-парком.
          </h1>
          <ul className="space-y-3 text-sm text-slate-300">
            <li className="flex gap-2.5">
              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-cyan-300" />
              Провижининг узлов по SSH: Xray, агент, systemd и firewall — одной операцией.
            </li>
            <li className="flex gap-2.5">
              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-cyan-300" />
              Per-site маршрутизация с allowlist выходов и fail-closed по умолчанию.
            </li>
            <li className="flex gap-2.5">
              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-cyan-300" />
              Российские сайты — напрямую, зарубежные — только через выбранные выходы.
            </li>
            <li className="flex gap-2.5">
              <span className="mt-1 size-1.5 shrink-0 rounded-full bg-cyan-300" />
              QR, VLESS-ссылки и умный профиль с RU-bypass для клиентов.
            </li>
          </ul>
        </div>
        <p className="relative text-xs text-slate-500">
          self-hosted · single admin · SQLite по умолчанию
        </p>
      </div>

      <div className="relative flex items-center justify-center p-6 sm:p-10">
        <button
          type="button"
          onClick={() => {
            toggleTheme();
            applyInitialTheme();
          }}
          className="absolute right-5 top-5 rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-label={resolved === "dark" ? "Светлая тема" : "Тёмная тема"}
        >
          {resolved === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
        </button>

        <div className="w-full max-w-sm space-y-7">
          <div className="space-y-1.5 text-center lg:text-left">
            <div className="mb-4 flex justify-center lg:hidden">
              <div className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
                <ShieldCheck className="size-6" aria-hidden />
              </div>
            </div>
            <h2 className="text-2xl font-semibold tracking-tight">Вход в панель</h2>
            <p className="text-sm text-muted-foreground">
              Используются данные из <code className="rounded bg-muted px-1 py-0.5 text-xs">ADMIN</code> и{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">PASSWORD</code>.
            </p>
          </div>

          {isMockMode() ? (
            <Alert variant="info">
              <AlertTitle>Мок-режим</AlertTitle>
              <AlertDescription>
                Backend не требуется: email <code>admin@vpn.local</code>, пароль{" "}
                <code>password12345</code>.
              </AlertDescription>
            </Alert>
          ) : null}

          <form className="space-y-4" onSubmit={handleSubmit} noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="login-email">Email</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  id="login-email"
                  type="email"
                  autoComplete="username"
                  placeholder="admin@example.com"
                  className="pl-9"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoFocus
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="login-password">Пароль</Label>
              <div className="relative">
                <LockKeyhole className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="••••••••••••"
                  className="px-9"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowPassword((current) => !current)}
                  aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            {error ? (
              <Alert variant="destructive">
                <AlertTitle>Не удалось войти</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}

            <Button type="submit" className="w-full" disabled={login.isPending}>
              {login.isPending ? <Loader2 className="animate-spin" /> : <KeyRound />}
              {login.isPending ? "Проверка…" : "Войти"}
            </Button>
          </form>

          <p className="text-center text-xs text-muted-foreground lg:text-left">
            Сессия хранится в HttpOnly-cookie. После входа все мутации защищены CSRF-токеном.
          </p>
        </div>
      </div>
    </div>
  );
}
