import type { Metadata } from "next";
import "./globals.css";
import SiteHeader from "@/components/SiteHeader";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Car2LEGO_3D | 多视图重建",
  description:
    "Car2LEGO 车辆设计工作室。创建你的车型设计，查看参考图与零件清单，下载 BrickLink Studio 文件。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen flex flex-col">
        <SiteHeader />
        <main className="flex-1">{children}</main>

        <footer className="site-footer">
          <div className="content-width">
            <p>LEGO® 是 LEGO Group 的注册商标。Car2LEGO 与 LEGO Group 无关联。</p>
            <div className="footer-bottom"><span>Car2LEGO</span><div><Link href="/">创作</Link><Link href="/sets">车型目录</Link></div><span>中国大陆 · 简体中文</span></div>
          </div>
        </footer>
      </body>
    </html>
  );
}
