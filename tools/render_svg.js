// Small render helper used for visual regression / evaluation.
//
//   npm install @resvg/resvg-js
//   node tools/render_svg.js out/2.svg /tmp/render2.png 1080
//
// The width argument is optional; omit it to render at the SVG's intrinsic
// size.  Fonts available to resvg/fontconfig are used for <text>.
const fs = require("fs");
const { Resvg } = require("@resvg/resvg-js");

const [, , input, output, widthArg] = process.argv;
if (!input || !output) {
  console.error("usage: node tools/render_svg.js input.svg output.png [width]");
  process.exit(2);
}
const width = widthArg ? parseInt(widthArg, 10) : undefined;
const svg = fs.readFileSync(input);
const resvg = new Resvg(svg, {
  fitTo: width ? { mode: "width", value: width } : undefined,
  font: { loadSystemFonts: true },
});
fs.writeFileSync(output, resvg.render().asPng());
console.log(JSON.stringify({ width: resvg.width, height: resvg.height }));
