import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => vi.restoreAllMocks());

test("mostra a mensagem que a API devolve", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ message: "Olá do teste" }), { status: 200 }),
  );
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Olá do teste" })).toBeInTheDocument();
});

test("avisa quando a API falha", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 500 }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "API indisponível" })).toBeInTheDocument();
});
