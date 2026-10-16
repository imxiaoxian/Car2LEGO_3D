/** @type {import('next').NextConfig} */
const nextConfig = {
  distDir: process.env.CAR2LEGO_DIST_DIR || ".next",
  eslint: { dirs: ["app", "components", "lib"] },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.CAR2LEGO_API_ORIGIN || "http://127.0.0.1:8767"}/api/:path*` }];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
};

export default nextConfig;
