import type { Metadata, Viewport } from "next";

import { AuthProvider } from "@/components/auth-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "CoursePilot｜证据优先的课程学习 Agent",
  description:
    "CoursePilot 以教师提供的课程资料为依据，为教师与学生建立可审核、可引用的课程学习闭环。",
  applicationName: "CoursePilot",
  icons: {
    icon: "/coursepilot-mark.svg",
  },
};

export const viewport: Viewport = {
  colorScheme: "light",
  themeColor: "#f3f1ea",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
