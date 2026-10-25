import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-start px-4 py-28 sm:px-6">
      <p className="eyebrow">Error 404</p>
      <h1 className="mt-3 text-6xl">Nothing here.</h1>
      <p className="mt-4 text-lg text-muted">This page doesn&apos;t exist, or it moved. Try one of these instead.</p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/" className="btn btn-primary">Home</Link>
        <Link href="/download" className="btn btn-ghost">Download</Link>
        <Link href="/guide" className="btn btn-ghost">Guide</Link>
      </div>
    </div>
  );
}
