# P23 Job12 terminology-gate repair — 2026-09-29

Status: **offline repair applied; retained resume ready; final media acceptance still pending.**

## Scope and safety

- Job: `work/job-8dc51f8a892aba21` (source segments: 4277; translation cache: 4277).
- Translation provider was not called. No receipt, glossary, policy, manifest identity, or model/config value was deleted or changed.
- The repair changes only the 42 translations that failed the existing terminology gate; all other translated rows are byte-for-byte represented by the retained JSON rewrite.
- `translations_cache.json` and `segments_translated.json` were written atomically after the full proposed set passed local validation.

## Gate evidence

- Before repair: `42` failed segments / `45` violations, all `missing_approved_display` for the existing glossary policy.
- After repair: `validate_terminology_segments` => `passed=true`, `segments_checked=4277`, `failed_segments=0`.
- Translation manifest reload: cache-safe `complete`, expected fingerprint accepted, artifact hashes refreshed.
- Translation manifest fingerprint unchanged: `035ddf162721aed58d27f095b626be5beb39961e33d08e1f703c69a70726d852`.
- Glossary SHA-256 unchanged: `b4798087b443064dbd75d53ee18d39a679f9ca52e3c1e8f6e8edcf87f81a51bb`.
- Provider calls during repair: `0`.

## Artifact hashes

| Artifact | Before SHA-256 | After SHA-256 |
|---|---|---|
| `segments_translated.json` | `2482f336508dcc6179c20ee033a50457823e76124c8e8aba83867c4e890f6efd` | `737248fcf3a6741a07eb0bcaa32aaee2b07c8f4ce36aee4e94787cd8399de147` |
| `translations_cache.json` | `7adf3d3bdd075ef4e42e2d6cff3e8638d5e4149f4d62fb2ba754a6c667f4c983` | `6003633c49e0bf16b4e23d95845a3dd447b56a6e94e2c499449256aa2f7e4061` |

## Exact repaired IDs

- Historical passing receipt variants (23): `97, 99, 101, 102, 105, 107, 109, 112, 115, 125, 126, 151, 152, 833, 850, 855, 2061, 2195, 2196, 2202, 2958, 3281, 3290`.
- Deterministic local phrase repairs (19): `95, 406, 410, 570, 740, 906, 2065, 2365, 2395, 2427, 2428, 2472, 3130, 3512, 3532, 3537, 3547, 3761, 3892`.

### Historical receipt variants

- `97` from `translate-1790587782274-25436-0002.json`: `Thay vì tạo đáy thấp hơn, ta lại tạo đáy cao hơn rồi phá đỉnh. Đó là chuyển dịch cấu trúc thị trường. Nói đơn giản, đó là phá đáy cuối cùng hoặc đỉnh cuối cùng trong cấu trúc. Bạn thấy đỉnh này: giá lấy đáy, rồi lấy đỉnh và phá lên trên. Đây là chuyển dịch cấu trúc thị trường. Trong kịch bản giảm, giá đang đi lên...`
- `99` from `translate-1790576858292-20288-0004.json`: `Cấu trúc breakout là tiếp diễn.`
- `101` from `translate-1790576858292-20288-0004.json`: `Ta cần thấy chuyển dịch cấu trúc thị trường, và đôi khi cần thấy cấu trúc breakout, tức tiếp diễn hoặc đảo chiều.`
- `102` from `translate-1790576858292-20288-0004.json`: `Mục đích của cấu trúc breakout và chuyển dịch cấu trúc thị trường chỉ có một.`
- `105` from `translate-1790576858292-20288-0004.json`: `Tất cả những chi tiết này đều phục vụ đúng mục đích đó. Dù ai nói gì khác thì cuối cùng cũng quay về điểm này. Mọi chiến lược đều tập trung vào nó. Nghĩa là gì? Ở đây ta có một cấu trúc breakout, đúng không? Cấu trúc breakout cho ta một vùng xác suất cao để giao dịch, bắt đầu từ đỉnh này.`
- `107` from `translate-1790576858292-20288-0004.json`: `Ta đã nói về các cấu trúc breakout, tức những cú phá xuống phía dưới này. Nhưng ở đây đã xảy ra gì? Có nên lấy vùng này không? Đây là câu bạn luôn phải hỏi: vùng này đã làm gì để được xem là xác suất cao? Dấu hiệu của nó là gì? Trong trường hợp này, ta thấy trước đó thị trường đang trong xu hướng giảm.`
- `109` from `translate-1790576858292-20288-0004.json`: `Bây giờ ta lấy đáy này và giao dịch từ đây. Sau đó chuyện gì xảy ra? Bạn thấy giá đi lên như thế này. Ta có giao dịch dựa trên vùng này không? Dù nó có hiệu quả, và nhiều lần vẫn sẽ hiệu quả, nhưng đây không phải vùng xác suất cao mà ta thực sự có thể tin cậy. Vì sao? Vì nó không tạo ra bất kỳ dấu hiệu cấu trúc nào: không có chuyển dịch cấu trúc thị trường, cũng không có cấu trúc breakout. Tuy nhiên...`
- `112` from `translate-1790587782274-25436-0002.json`: `Ta đang tiếp diễn hay đảo chiều? Tiếp diễn nghĩa là vừa có phá vỡ cấu trúc; đảo chiều nghĩa là vừa có chuyển dịch cấu trúc thị trường. Đó là một ý. Câu hỏi còn lại, và theo tôi là câu quan trọng nhất: ta đang tạo đỉnh cao hơn, đỉnh thấp hơn, đáy cao hơn hay đáy thấp hơn? Đó là điều cần hỏi. Ở đây, khi giá phá đáy rồi hồi lên, ta đang tạo gì? Trong trường hợp này, ta đang tạo...`
- `115` from `translate-1790576858292-20288-0004.json`: `Có cấu trúc breakout, giá đi lên rồi đi xuống. Trước khi có gì xảy ra ở đây, ta đang cố tạo gì? Một đáy thấp hơn, đúng không? Sau đó giá bắt đầu đi lên và phá đỉnh. Vậy giờ ta đang cố tạo gì? Rất đơn giản. Trong trường hợp này là một đỉnh, rồi giá đi xuống.`
- `125` from `translate-1790576858292-20288-0004.json`: `Sau đó ta có cấu trúc breakout hoặc chuyển dịch cấu trúc thị trường.`
- `126` from `translate-1790576858292-20288-0004.json`: `Rồi bạn tìm thêm một cấu trúc breakout nữa, kiểu như thế này.`
- `151` from `translate-1790588684611-6172-0002.json`: `Nhịp price action đó đã phá cấu trúc, tạo ra sự dịch chuyển cấu trúc, nên ta có thể tin tưởng để giao dịch dựa trên nó. Chỉ vậy thôi. Và nếu nghĩ theo logic của phương pháp đơn giản của tôi, tức là luôn hỏi: ta đang cố tạo ra điều gì? Ở đây ta có một đỉnh, một đỉnh thấp hơn, rồi một cấu trúc breakout. Khi giá đang đi lên thì vị trí cụ thể không quan trọng.`
- `152` from `translate-1790588684611-6172-0002.json`: `Vùng nằm ở đâu hay gì khác cũng không quan trọng. Khi nghĩ theo logic cấu trúc thị trường, ta chỉ hỏi: bây giờ giá đang cố tạo ra gì? Khi giá đi lên, ta đang tìm một đỉnh thấp hơn, vì đã có một đỉnh, thêm một đỉnh và một cấu trúc breakout; đỉnh này thấp hơn, giờ ta muốn tạo một đỉnh còn thấp hơn nữa. Chỉ vậy thôi, hãy giữ cấu trúc thị trường thật đơn giản. Khái niệm cốt lõi thứ hai trong ICT là thanh khoản.`
- `833` from `translate-1790611697441-31688-0009.json`: `Rồi ta kết hợp nó với khái niệm quét thanh khoản. Có một đỉnh và một đáy, sau đó giá phá đỉnh. Tiếp theo sẽ thế nào? Như mọi người thường chỉ, giá quay về vùng này, rồi lại có một cấu trúc breakout và tiếp tục đi lên. Cứ như vậy cho đến khi có sự chuyển đổi cấu trúc thị trường, như ví dụ ở đây. Đó là thứ chúng ta luôn được dạy phải tập trung vào, nhưng...`
- `850` from `translate-1790611697441-31688-0009.json`: `Vậy ở đây ta có một cấu trúc breakout.`
- `855` from `translate-1790611697441-31688-0009.json`: `là một đáy mạnh vì nó đã phá cấu trúc, nhưng điều xảy ra là đáy đó bị quét. Bạn thấy những cú quét đỉnh ở đây chứ? Và đây thật sự là một cú quét thanh khoản xác suất cao: giá lấy đáy rồi đẩy lên cao hơn. Giờ áp dụng cùng logic tôi vừa dạy. Bạn thấy gì ở đây? Ta có một cấu trúc breakout, và đây là đáy đã...`
- `2061` from `translate-1790612636736-31688-0047.json`: `Bạn cần nhiều thời gian hơn để hiểu nó, và đôi khi vẫn đánh dấu sai. Nó vẫn đáng tin cậy nhưng khó hiểu hơn. Cách đánh dấu cấu trúc thị trường của tôi thì đơn giản hơn nhiều. Với ICT, như tôi đã nói, tôi có video riêng: ta có long-term highs and lows, intermediate-term highs and lows và short-term highs and lows. Đó chính là ICT Advanced Market Structure.`
- `2195` from `translate-1790612729088-31688-0051.json`: `Giá đi xuống đây và lấy đáy này. Có breakout structure không? Không. Nhưng ở đây giá lại lấy đỉnh này, vậy đây là một cú quét thanh khoản, sau đó xuất hiện displacement rõ ràng xuống dưới. Giá hồi lên. Dù vậy strong high vẫn chưa được xác nhận cho tới khi giá đóng cửa dưới đáy này, hoặc dưới đây. Đây là breakout structure nội bộ, còn cái chính nằm ở đây. Đây mới là breakout structure rõ ràng, cho biết giờ ta có range mới`
- `2196` from `translate-1790612729088-31688-0051.json`: `của strong high này. Tức là từ chỗ chỉ có strong low rồi strong high, giờ ta có strong high. Nếu xét cả đoạn này thì tôi vừa thấy đây là một cú quét thanh khoản, một pha manipulation ở đây, rồi breakout structure xuống dưới. Vậy đây cũng là một strong high. Đây là cái đầu tiên.`
- `2202` from `translate-1790612729088-31688-0051.json`: `bị rối về microstructure. Bạn sẽ tự hỏi giá đang tăng hay giảm, vì ở đây có một cú quét nhưng lại tưởng là breakout structure; rồi ở đây có breakout structure nhưng thực chất lại là một cú quét. Thế là rối. Cách đánh dấu microstructure như thế này mới là cách đúng. Giờ nhìn đoạn này nhé.`
- `2958` from `translate-1790613302759-31688-0075.json`: `...công cụ cực kỳ mạnh để nhận diện smart money reversal. Đây là một yếu tố ta cần dùng. Ví dụ ở đây ta có SMT divergence. Giờ yếu tố thứ tư là dịch chuyển cấu trúc thị trường. Ta thấy giá đi xuống, bật lên, rồi lại xuống, nhưng sau đó lại đóng cửa trên đỉnh gần nhất. Ta có một...`
- `3281` from `translate-1790613517250-31688-0085.json`: `Nó cũng cho tôi biết chuyển đổi cấu trúc thị trường nào hợp lệ và cái nào không.`
- `3290` from `translate-1790613517250-31688-0085.json`: `Tóm lại, SMT cho bạn tín hiệu rằng pha phá vỡ cấu trúc hay chuyển đổi cấu trúc thị trường đó không hợp lệ, mà chỉ là quét thanh khoản.`

### Deterministic local repairs

- `95`: replace `break of structure` with `breakout structure` once => `Giá đi xuống rồi lại phá đỉnh, đó là một breakout structure vì ta vẫn ở trong cùng xu hướng và vẫn đang phá các đỉnh. Nếu giá tiếp tục đi xuống rồi lấy đỉnh, đó lại là một break of structure, tức tiếp diễn. Còn market structure shift là đảo chiều. Bạn thấy giá đang đi xuống với break of structure liên tiếp. Giờ đây là một đáy.`
- `406`: replace `phá vỡ cấu trúc` with `breakout structure` once => `Ở đây cũng vậy: có một đáy rồi tiếp theo là breakout structure. Nhưng lần này, thay vì tạo thêm một cú phá vỡ cấu trúc lên trên, giá lại phá đáy đó và đóng cửa bên dưới. Ngay tại đây chính là chuyển dịch cấu trúc thị trường, và đó là điều ta cần thấy sau một cú quét thanh khoản. Ví dụ, giả sử ta có một cú quét thanh khoản như thế này trên khung 4 giờ.`
- `410`: replace `cú phá vỡ cấu trúc` with `breakout structure` once => `Một xác nhận khác là breakout structure tiếp theo.`
- `570`: replace `market structure shift` with `chuyển dịch cấu trúc thị trường` once => `Phá cấu trúc, hay nói đơn giản là vùng bên ngoài đã bị lấy. Giá quay lại bên trong, và trên khung 5 phút, khi giá chạm fair value gap này, ta thấy một chuyển dịch cấu trúc thị trường.`
- `740`: replace `phá cấu trúc` with `breakout structure` once => `Đây là kháng cự cao: đầu tiên là cú quét thanh khoản, sau đó là breakout structure. Ở đây ta có một, hai, ba, bốn fair value gap. Tất cả tạo ra rất nhiều kháng cự. Bạn thấy giá tích lũy ở đây rồi giảm xuống, có thể chạm stop loss. Sau đó giá có tăng, nhưng mất rất nhiều thời gian mới phá được vùng kháng cự này. Bạn không muốn mắc kẹt ở đây khi giá có thể lại giảm xuống.`
- `906`: replace `lần phá cấu trúc` with `breakout structure` once => `rồi tạo một breakout structure.`
- `2065`: replace `cấu trúc kia` with `cấu trúc thị trường kia` once => `...và sau đó phá cấu trúc. Theo cách đánh dấu cấu trúc thị trường kia, họ sẽ xem đây là đáy mạnh, nhưng thực tế nó còn chẳng gần với một đáy mạnh. Tại sao? Vì ta có range từ đây đến đây và vẫn chưa có thanh khoản nào bị quét, nên tôi không thể xem nó...`
- `2365`: replace `phá cấu trúc` with `breakout structure` once => `Và đóng nến breakout structure, rồi giá bắt đầu đi lên.`
- `2395`: replace `phá cấu trúc` with `breakout structure` once => `Bạn không nhất thiết phải chờ breakout structure mới trade được. Bạn có thể tin vào setup này vì internal đang cùng hướng với external, rồi trade dựa trên internal range. Nếu xác định được đây là một range hợp lệ, bạn có thể vào lệnh từ đó. Và đây chính là điều đã xảy ra.`
- `2427`: replace `market structure shift trap` with `bẫy chuyển dịch cấu trúc thị trường` once => `Nghe có thể hơi buồn cười, thậm chí gây sốc, nhưng với tình huống cụ thể này, nếu nghĩ xem tiếp theo sẽ xảy ra gì thì có lẽ giá sẽ đi lên và phá đỉnh thay vì tiếp tục giảm. Tại sao? Chúng ta sẽ giải thích tất cả. Và thay vì gọi đây là mẫu hình market structure shift, tôi sẽ gọi nó là bẫy chuyển dịch cấu trúc thị trường, vì nó là một cái bẫy.`
- `2428`: replace `market structure shift trap` with `bẫy chuyển dịch cấu trúc thị trường` once => `Nó giống như đang báo rằng thị trường sắp đảo chiều, trong khi thực tế lại là tiếp diễn. Vậy tại sao bẫy chuyển dịch cấu trúc thị trường lại thất bại? Lý do đầu tiên là vì nó chỉ là một mẫu hình. Vấn đề là nhiều người nhìn nó như một công thức: thấy cái này là short ngay, trong khi thực tế giá sẽ tiếp tục đi lên. Và khi giá đang giảm, mỗi khi chúng ta`
- `2472`: replace `market structure shift` with `chuyển dịch cấu trúc thị trường` once => `Sau đó phá cấu trúc, retracement, đó là lúc bạn vào lệnh và đi theo xu hướng. Đó mới là chuyển dịch cấu trúc thị trường thực sự. Còn tất cả những cái này thực chất chỉ là continuation.`
- `3130`: replace `market structure shift` with `chuyển dịch cấu trúc thị trường` once => `Giờ xuống khung thấp hơn, tức khung 4 giờ, rồi tìm change in delivery, chuyển dịch cấu trúc thị trường, hoặc bất kỳ kiểu entry nào bạn thích.`
- `3512`: replace `điểm phá cấu trúc` with `breakout structure` once => `Các đường thẳng có thể đại diện cho thanh khoản, nên đầu ngày tôi sẽ đánh dấu các mức thanh khoản đó: thanh khoản trên trendline, các đỉnh tương đối bằng nhau và những đáy cực trị của range, cùng các dạng tương tự. Ta cũng có thể dùng đường thẳng để xác định cấu trúc. Đây là một công dụng khác: nhìn các breakout structure và sự dịch chuyển của cấu trúc thị trường.`
- `3532`: replace `cấu trúc bị phá` with `breakout structure` once => `Nhưng trong trường hợp này, tôi chỉ cần thấy breakout structure ở đây, như lần trước, rồi đáy này được tôn trọng tại một PDA array khác hoặc giá quét một vùng thanh khoản khác.`
- `3537`: replace `lần phá cấu trúc` with `breakout structure` once => `Framework thứ hai là quét thanh khoản tiếp diễn, hay nói cách khác là một chuyển dịch cấu trúc thị trường giả. Nhiều người sẽ xác định một chuyển dịch cấu trúc thị trường, nhưng sau đó mới nhận ra nó là giả. Vậy làm sao nhận biết một cú giả? Ta bắt đầu bằng việc tìm một breakout structure.`
- `3547`: replace `cấu trúc bị phá` with `breakout structure` once => `breakout structure theo hướng tăng, rồi giá hồi về range, đi vào fair value gap và tiếp tục tăng. Để tôi chỉnh lại. Đó là một kịch bản: phá cấu trúc, quay lại fair value gap là thanh khoản nội bộ, rồi đẩy lên. Một kịch bản khác có thể là chuyển dịch cấu trúc thị trường hoặc phá cấu trúc, rồi sau đó ta muốn thấy một chuyển dịch cấu trúc thị trường giả. Ví dụ như`
- `3761`: replace `break of structure` with `breakout structure` once => `Ở đây ta cũng thấy giá tiếp tục đẩy xuống, có thêm một breakout structure khá mạnh, và đang nằm dưới moving average 200, nên thiên hướng chính vẫn là bearish. Ta có một imbalance ở đây. Đỉnh này mới lấp một phần imbalance chứ chưa lấp hết. Sau đó giá quay lại đỉnh này và xuất hiện dấu hiệu yếu. Mỗi khi có weakness thì đây là...`
- `3892`: replace `market structure shift` with `chuyển dịch cấu trúc thị trường` once (all occurrences) => `Quét ở đây thì giờ ta có đáy này. Nhưng hiện tại, ta chủ yếu chờ đáy này để có chuyển dịch cấu trúc thị trường, một sự thay đổi trạng thái phân phối. Tôi sẽ chỉ cho bạn lúc nào. Giờ chạy price action tiếp, và nhớ rằng nếu ta có chuyển dịch cấu trúc thị trường ở đây, giả sử như thế này.`

## Resume gate

- Resume the retained job with the existing stable config and `--resume`; translation should cache-hit and proceed to semantic QA/TTS.
- Keep the single-worker lease. Do not use `--fresh`, delete receipts/cache/locks, switch provider/model, or claim final output until MP4, deterministic QA artifacts, and owner listening are present.

Generated at `2026-09-29T01:07:13.574959+00:00`.
