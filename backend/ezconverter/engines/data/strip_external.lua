-- Trusted filter shipped with EZConverter. Untrusted documents must never make Pandoc read local or
-- remote resources, so images are replaced by their alt text and raw passthrough content is removed.
function Image(el)
  return el.caption
end
function RawInline() return {} end
function RawBlock() return {} end
