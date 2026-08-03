/**
 * Ambient module declarations for raster image imports.
 *
 * Docusaurus' `@docusaurus/module-type-aliases` ships a `*.svg` declaration
 * (Webpack returns it as a React component) but no entry for raster formats.
 * Static asset imports (`import foo from "@site/static/img/foo.png"`) are
 * processed by Webpack's `asset/resource` rule and exposed as the asset's
 * final hashed URL string, so we mirror that shape here so the call sites
 * stay typed.
 */
declare module "*.png" {
  const src: string;
  export default src;
}

declare module "*.jpg" {
  const src: string;
  export default src;
}

declare module "*.jpeg" {
  const src: string;
  export default src;
}

declare module "*.gif" {
  const src: string;
  export default src;
}

declare module "*.webp" {
  const src: string;
  export default src;
}
