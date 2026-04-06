import { getState } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  const encoder = new TextEncoder();
  let lastUpdated = "";

  const stream = new ReadableStream({
    start(controller) {
      const interval = setInterval(() => {
        try {
          const state = getState("site_state") as Record<string, unknown> & { updated_at?: string } | null;
          const updatedAt = state?.updated_at as string || "";

          if (updatedAt !== lastUpdated) {
            lastUpdated = updatedAt;
            const data = `data: ${JSON.stringify(state)}\n\n`;
            controller.enqueue(encoder.encode(data));
          }
        } catch {
          clearInterval(interval);
          controller.close();
        }
      }, 2000);

      // Cleanup on disconnect
      const cleanup = () => clearInterval(interval);
      controller.enqueue(encoder.encode(": connected\n\n"));

      // Close after 5 minutes (client should reconnect)
      setTimeout(() => {
        cleanup();
        try { controller.close(); } catch { /* already closed */ }
      }, 5 * 60 * 1000);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
