const health = {
  status: "ok",
  service: "coursepilot-web",
  milestone: "week-1",
} as const;

export function GET() {
  return Response.json(health, {
    headers: {
      "Cache-Control": "no-store",
    },
  });
}
