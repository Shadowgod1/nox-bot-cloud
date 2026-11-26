/** Bot address from the company name: «ТОО "Салон Аяна"» -> salon-ayana. */

const MAP: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm',
  н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'sch',
  ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
  // Kazakh letters
  ә: 'a', ғ: 'g', қ: 'k', ң: 'n', ө: 'o', ұ: 'u', ү: 'u', һ: 'h', і: 'i',
}

// legal forms are not part of the name (whole words only)
const LEGAL_FORMS = /(^|[\s"'«»“”„.,()-])(тоо|ип|ооо|оао|зао|ао|пао|чп|тов|кх|llp|llc|ltd|inc|too|ip)(?=$|[\s"'«»“”„.,()-])/giu

export const SLUG_MAX = 32

export function slugFromCompany(company: string): string {
  const name = company.toLowerCase().replace(LEGAL_FORMS, ' ')
  let out = ''
  for (const ch of name) out += MAP[ch] ?? ch
  out = out
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (!out) return ''
  if (!/^[a-z]/.test(out)) out = `bot-${out}`
  out = out.slice(0, SLUG_MAX).replace(/-+$/g, '')
  if (out.length < 3) out = `${out}-bot`
  return out
}
