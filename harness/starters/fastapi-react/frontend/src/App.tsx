import { useEffect, useState } from "react";

export default function App() {
  const [message, setMessage] = useState<string>("carregando…");

  useEffect(() => {
    fetch("/api/hello")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: { message: string }) => setMessage(data.message))
      .catch(() => setMessage("API indisponível"));
  }, []);

  return (
    <main>
      <h1>{message}</h1>
    </main>
  );
}
