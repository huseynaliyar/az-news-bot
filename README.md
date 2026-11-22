# Avtomatik Azərbaycan, Türkiyə və Dünya Xəbər Botu (RSS → AI → Telegram)

Bu sistem **tam pulsuz** şəkildə işləyən avtomatik xəbər botudur:
- **Azərbaycan** (`Oxu.az`, `AzerNews`, `Day.az`, `Google News AZ`), **Türkiyə** (`TRT Haber`, `NTV Gündem`, `NTV Dünya`, `CNN Türk`, `Google News TR`) və **Dünya** (`BBC World`, `Al Jazeera`, `Google News World`) RSS lentlərini oxuyur.
- Eyni hadisəni 10-15 fərqli sayt yazsa belə, süni intellekt (**Gemini** və ya **Groq** pulsuz API) onları **tək bir xəbərdə birləşdirir**, Azərbaycan dilində səlis xülasə hazırlayır və doğruluq statusunu (`✅ Təsdiqlənmiş` / `⚠️ Tək mənbə`) qeyd edir.
- **Çoxgünlük yaddaş (`data/history.json`)** sayəsində 3 gün sonra eyni xəbər təkrar gəlsə belə onu avtomatik bloklayır (yalnız ciddi yeni inkişaf varsa `🔄 Yenilənmə` etiketi ilə paylaşır).
- Hazır xəbərləri avtomatik olaraq **Telegram** kanalınıza və ya qrupunuza göndərir.

---

## Pulsuz Server Seçimi: GitHub Actions vs. PythonAnywhere

| Platforma | Qiymət | Limitlər | RSS Mənbələrinə Çıxış | Tövsiyə |
| :--- | :--- | :--- | :--- | :--- |
| **GitHub Actions** | **$0 (kart tələb etmir)** | Public repo: **limitsiz**, Private repo: ayda **2,000 dəqiqə** (hər 30 dəq-dən bir işləməyə artıqlaması ilə bəsdir) | **Tam açıq** (bütün AZ, TR, dünya saytları işləyir) | **Ən yaxşı seçim (hazır quraşdırılıb)** |
| **PythonAnywhere** | $0 (Beginner plan) | Gündə yalnız **1 scheduled task** (məs. gündə 1 dəfə işləyir) | **Məhdud (whitelist)** — bir çox AZ/TR saytının RSS-i pulsuz planda bloklana bilər | Yalnız gündə 1 dəfə və whitelist-də olan saytlar üçün |

---

## 5 Dəqiqəlik Quraşdırma (GitHub Actions — 100% Pulsuz)

### 1. Telegram Botu yaradın
1. Telegram-da **@BotFather** botuna yazın: `/newbot`
2. Botun adını və istifadəçi adını seçin, sizə verilən **`TELEGRAM_BOT_TOKEN`** açarını yadda saxlayın.
3. Botu öz Telegram kanalınıza **Admin** (mesaj paylaşmaq icazəsi ilə) əlavə edin.
4. Kanalın istifadəçi adını (məsələn, `@menim_xeber_kanalim`) və ya özəl kanal ID-sini (`-100...`) qeyd edin.

### 2. Pulsuz AI API açarı alın (kart tələb etmir)
Ən azı birini götürün (hər ikisini əlavə etsəniz, biri limitə çatanda sistem avtomatik digərinə keçir):
- **Google Gemini (Tövsiyə olunur):** [Google AI Studio](https://aistudio.google.com/apikey) → *Create API Key* (`gemini-3.5-flash-lite` pulsuz planda gündə 500 sorğu verir).
- **Groq (Ehtiyat və ya əsas):** [Groq Console](https://console.groq.com/keys) → *Create API Key* (`llama-3.3-70b-versatile` pulsuz və çox sürətlidir).

### 3. GitHub-a yükləyin və avtomatlaşdırın
1. GitHub-da yeni repozitoriya yaradın (Public və ya Private) və bu qovluqdakı bütün faylları ora yükləyin.
2. Repozitoriyada **Settings → Secrets and variables → Actions → New repository secret** bölməsinə daxil olub aşağıdakıları əlavə edin:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY` (və/və ya `GROQ_API_KEY`)
3. **Actions** bölməsinə keçin → **Avtomatik Xəbər Botu** → **Run workflow** düyməsinə basıb ilk dəfə test edin.
4. Bundan sonra bot **hər 30 dəqiqədən bir** avtomatik işləyəcək və `data/history.json` yaddaş faylını özü yeniləyəcək!

---

## PythonAnywhere-də işlətmək istəsəniz
1. Faylları PythonAnywhere-ə yükləyin və Bash konsolunda kitabxanaları quraşdırın:
   ```bash
   pip install --user -r requirements.txt
   ```
2. `.env.example` faylını `.env` adına kopyalayıb API açarlarınızı daxil edin.
3. **Tasks** bölməsində gündəlik tapşırıq əlavə edin:
   ```bash
   cd ~/az-news-bot && python3 -m src.main
   ```
*(Qeyd: PythonAnywhere-in pulsuz planında xarici domenlərə çıxış whitelist ilə məhdudlaşdığı üçün bəzi yerli/türk RSS lentləri açılmaya bilər; buna görə GitHub Actions daha problemsizdir.)*

---

## Fərdiləşdirmə
- **Yeni sayt əlavə etmək:** `feeds.json` faylına istədiyiniz RSS linkini əlavə edib çıxara bilərsiniz.
- **Yalnız təsdiqlənmiş xəbərləri paylaşmaq:** GitHub **Settings → Secrets and variables → Actions → Variables** bölməsində `ONLY_VERIFIED` dəyərini `true` edin.
- **Tezliyi dəyişmək:** `.github/workflows/news_bot.yml` faylında `cron: "*/30 * * * *"` hissəsini dəyişin (məs. saatlıq üçün `"0 * * * *"`).
