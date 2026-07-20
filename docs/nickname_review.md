# Nickname abbreviation review

Community nickname = `<abbrev><focus-index>` — e.g. `mizu3` is Mizuki Akiyama's
**3rd** focus event. Only the **abbrev** (per character) is fixed here; the number
is the character's Nth focus event in release order, derived automatically from
the master DB.

**Please edit the `Abbrev` column (or the notes column) wherever it's wrong.**
Confirmed correct so far: `13 = kasa` (Tsukasa Tenma), `20 = mizu` (Mizuki Akiyama).
Everything else was seeded and needs your check. Source of truth:
`src/sekai_story_indexer/source/nicknames.py` (`CHARACTER_ID_TO_ABBREV`); overrides
can also go in a `nicknames.json`.

| id | Unit | JP name | EN name | Abbrev | Example | Correct? / fix |
|----|------|---------|---------|--------|---------|----------------|
| 1 | Leo/need | 星乃一歌 | Ichika Hoshino | `ichi` | `ichi1` | |
| 2 | Leo/need | 天馬咲希 | Saki Tenma | `saki` | `saki1` | |
| 3 | Leo/need | 望月穂波 | Honami Mochizuki | `hona` | `hona1` | |
| 4 | Leo/need | 日野森志歩 | Shiho Hinomori | `shiho` | `shiho1` | |
| 5 | MORE MORE JUMP! | 花里みのり | Minori Hanasato | `mino` | `mino1` | |
| 6 | MORE MORE JUMP! | 桐谷遥 | Haruka Kiritani | `haru` | `haru1` | |
| 7 | MORE MORE JUMP! | 桃井愛莉 | Airi Momoi | `airi` | `airi1` | |
| 8 | MORE MORE JUMP! | 日野森雫 | Shizuku Hinomori | `shizu` | `shizu1` | |
| 9 | Vivid BAD SQUAD | 小豆沢こはね | Kohane Azusawa | `koha` | `koha1` | |
| 10 | Vivid BAD SQUAD | 白石杏 | An Shiraishi | `an` | `an1` | |
| 11 | Vivid BAD SQUAD | 東雲彰人 | Akito Shinonome | `aki` | `aki1` | ✅ confirmed |
| 12 | Vivid BAD SQUAD | 青柳冬弥 | Toya Aoyagi | `toya` | `toya1` | |
| 13 | Wonderlands x Showtime | 天馬司 | Tsukasa Tenma | `kasa` | `kasa1` | ✅ confirmed |
| 14 | Wonderlands x Showtime | 鳳えむ | Emu Otori | `emu` | `emu1` | |
| 15 | Wonderlands x Showtime | 草薙寧々 | Nene Kusanagi | `nene` | `nene1` | |
| 16 | Wonderlands x Showtime | 神代類 | Rui Kamishiro | `rui` | `rui1` | |
| 17 | Nightcord at 25:00 | 宵崎奏 | Kanade Yoisaki | `kana` | `kana1` | |
| 18 | Nightcord at 25:00 | 朝比奈まふゆ | Mafuyu Asahina | `mafu` | `mafu1` | |
| 19 | Nightcord at 25:00 | 東雲絵名 | Ena Shinonome | `ena` | `ena1` | |
| 20 | Nightcord at 25:00 | 暁山瑞希 | Mizuki Akiyama | `mizu` | `mizu1` | ✅ confirmed |
| 21 | Virtual Singer | 初音ミク | Hatsune Miku | `miku` | `miku1` | |
| 22 | Virtual Singer | 鏡音リン | Kagamine Rin | `rin` | `rin1` | |
| 23 | Virtual Singer | 鏡音レン | Kagamine Len | `len` | `len1` | |
| 24 | Virtual Singer | 巡音ルカ | Megurine Luka | `luka` | `luka1` | |
| 25 | Virtual Singer | MEIKO | MEIKO | `meiko` | `meiko1` | |
| 26 | Virtual Singer | KAITO | KAITO | `kaito` | `kaito1` | |
