"""Strings of World Interaction v1 (inspecting parcels, public places and districts). Merged into texts.FA / texts.EN."""

FA_WORLD = {
    # --- the panels
    "wi.title.parcel": "🔎 <b>قطعه {id}</b> — {city}",
    "wi.title.landmark": "🔎 <b>{name} {id}</b> — {city}",
    "wi.title.district": "🔎 <b>منطقه {id}</b> — {city}",
    "wi.f.type": "نوع", "wi.f.district": "منطقه", "wi.f.status": "وضعیت", "wi.f.road": "دسترسی جاده‌ای", "wi.f.where": "موقعیت",
    "wi.f.size": "مساحت", "wi.f.parcel": "قطعه", "wi.f.place": "مکان عمومی", "wi.f.near": "قطعه‌های نزدیک", "wi.f.lots": "قطعه‌ها",
    "wi.f.green": "فضای سبز", "wi.f.places": "مکان‌های عمومی", "wi.f.centre": "مرکز", "wi.f.density": "تراکم",
    "wi.type.lot.residential": "قطعه‌ی مسکونی", "wi.type.lot.commercial": "قطعه‌ی تجاری", "wi.type.lot.industrial": "قطعه‌ی صنعتی",
    "wi.type.lot.civic": "قطعه‌ی عمومی", "wi.type.lot.park": "قطعه‌ی پارک",
    "wi.type.green.park": "پارک", "wi.type.green.other": "فضای سبز", "wi.type.landmark": "مکان عمومی — {name}",
    "wi.status.vacant": "زمین خالی", "wi.status.building": "ساخته‌شده", "wi.status.green": "فضای سبز", "wi.status.landmark": "مکان عمومی",
    "wi.road.direct": "متصل — {road}", "wi.road.inner": "بدون اتصال مستقیم — داخل بلوک؛ حدود {d} تا نزدیک‌ترین جاده",
    "wi.xy": "{x}، {y}",
    "wi.where": "مختصات {x}، {y}", "wi.where.near": "مختصات {x}، {y} — نزدیک {name} ({id})",
    "wi.size": "{n}",
    "wi.density.dense": "متراکم", "wi.density.open": "باز و کم‌تراکم",
    "wi.lots": "{n} (زمین خالی {vacant} · ساخته‌شده {built})", "wi.green": "{n} قطعه",
    "wi.foot": "ℹ️ این فقط اطلاعاته؛ خرید زمین و ساخت‌وساز هنوز فعال نیست.",
    "wi.back": "🗺 نقشه‌ی کامل: «مپ»",
    # --- errors
    "wi.unknown": "❌ شناسه‌ی <b>{id}</b> توی شهر تو پیدا نشد.\nشناسه‌ی مکان‌های عمومی زیر نقشه‌ی «مپ» نوشته شده؛ مثلاً <code>مپ {example}</code>",
    "wi.bad": "🔎 این شناسه درست نیست. قطعه مثل <code>مپ P14</code>، مکان عمومی مثل <code>مپ L1</code> و منطقه مثل <code>مپ D2</code>.\nبرای نقشه‌ی کامل فقط «مپ» بنویس.",
    # --- the hint under the plain map
    "map.hint": "🔎 جزئیات هر قطعه یا مکان: «مپ شناسه»، مثلاً <code>مپ {example}</code>\n📍 {places}",
}

EN_WORLD = {
    "wi.title.parcel": "🔎 <b>Parcel {id}</b> — {city}",
    "wi.title.landmark": "🔎 <b>{name} {id}</b> — {city}",
    "wi.title.district": "🔎 <b>District {id}</b> — {city}",
    "wi.f.type": "Type", "wi.f.district": "District", "wi.f.status": "Status", "wi.f.road": "Road access", "wi.f.where": "Location",
    "wi.f.size": "Size", "wi.f.parcel": "Parcel", "wi.f.place": "Public place", "wi.f.near": "Nearby parcels", "wi.f.lots": "Parcels",
    "wi.f.green": "Green space", "wi.f.places": "Public places", "wi.f.centre": "Centre", "wi.f.density": "Density",
    "wi.type.lot.residential": "Residential parcel", "wi.type.lot.commercial": "Commercial parcel", "wi.type.lot.industrial": "Industrial parcel",
    "wi.type.lot.civic": "Civic parcel", "wi.type.lot.park": "Park parcel",
    "wi.type.green.park": "Park", "wi.type.green.other": "Green space", "wi.type.landmark": "Public place — {name}",
    "wi.status.vacant": "Empty land", "wi.status.building": "Built", "wi.status.green": "Green space", "wi.status.landmark": "Public place",
    "wi.road.direct": "Connected — {road}", "wi.road.inner": "No direct connection — inside a block; about {d} from the nearest road",
    "wi.xy": "{x}, {y}",
    "wi.where": "Coordinates {x}, {y}", "wi.where.near": "Coordinates {x}, {y} — near {name} ({id})",
    "wi.size": "{n}",
    "wi.density.dense": "Dense", "wi.density.open": "Open, low density",
    "wi.lots": "{n} (empty {vacant} · built {built})", "wi.green": "{n} parcels",
    "wi.foot": "ℹ️ Information only: buying land and building are not available yet.",
    "wi.back": "🗺 Full map: “map”",
    "wi.unknown": "❌ ID <b>{id}</b> was not found in your city.\nThe public places' IDs are listed under the «map» picture; for example <code>map {example}</code>",
    "wi.bad": "🔎 That is not a valid ID. A parcel looks like <code>map P14</code>, a public place like <code>map L1</code> and a district like <code>map D2</code>.\nFor the full map just send “map”.",
    "map.hint": "🔎 Details of any parcel or place: “map ID”, for example <code>map {example}</code>\n📍 {places}",
}
