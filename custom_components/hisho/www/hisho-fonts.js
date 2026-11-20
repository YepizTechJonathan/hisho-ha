// The Hisho theme's fonts (SIL Open Font License), served by the hisho integration: one stylesheet in the page's
// head, so every card's shadow DOM can use them too.
if (!document.querySelector('link[data-hisho-fonts]')) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/hisho_static/fonts/fonts.css";
  link.dataset.hishoFonts = "";
  document.head.appendChild(link);
}
