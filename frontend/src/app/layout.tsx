import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "知寻 ZhiSeek — 知乎低估回答探测器",
  description: "不做质量裁判，做低估探测器——挖掘知乎上「信息独特却零曝光」的回答",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
