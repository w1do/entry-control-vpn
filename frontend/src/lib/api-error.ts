/** Ошибка API: HTTP-статус + машиночитаемый код из envelope. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status = 0, code = "http_error") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Человекочитаемое сообщение об ошибке для toast/форм. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Неизвестная ошибка";
}

export function errorCode(error: unknown): string {
  return error instanceof ApiError ? error.code : "unknown_error";
}
