# Hướng dẫn upload bản sửa

Gói này sửa lỗi GitHub Actions không tìm thấy `pikajieqi-native` và
`verify_engine.sh`.

## Các file phải có trên nhánh `main`

- `.github/workflows/cup_bot.yml`
- `.github/workflows/n17.yml`
- `.gitignore`
- `cup_bot_jieqi.py`
- `n17.py`
- `pikajieqi-native`
- `verify_engine.sh`

Repo phải giữ nguyên file `zai_jieqi_master.nnue` đang có sẵn.

## Cách upload bằng Git

Chép toàn bộ nội dung gói này vào thư mục repo, giữ nguyên cấu trúc thư mục, rồi:

```bash
git add .github/workflows/cup_bot.yml .github/workflows/n17.yml \
  .gitignore cup_bot_jieqi.py n17.py pikajieqi-native verify_engine.sh
git commit -m "fix: include bundled engine required by workflows"
git push origin main
```

## Cách upload bằng giao diện GitHub

1. Giải nén ZIP trên máy.
2. Upload các file ở thư mục gốc vào gốc repo.
3. Với hai file workflow, mở lần lượt đường dẫn sau trên GitHub và thay nội dung:
   - `.github/workflows/cup_bot.yml`
   - `.github/workflows/n17.yml`
4. Đảm bảo GitHub hiển thị `pikajieqi-native` và `verify_engine.sh` ở gốc repo.
5. Commit trực tiếp vào nhánh `main`, sau đó chạy lại Actions.

Không upload nguyên file ZIP vào repo vì GitHub không tự giải nén ZIP.
