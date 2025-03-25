# findLocatinAPI

## dev

```
uvicorn app.main:app --reload
```

### API テスト

`http://127.0.0.1:8000/docs`の Try out で以下実行

```json
{
  "prefecture": "県",
  "city": "市or区"
}
```

### API 情報

Google Map API を使用
Google Cloud で API 確認する

<details>
<summary>memo</summary>

- テスト実行時めちゃくちゃ重いので処理速度を早くする
- データの取得項目の見直し

</details>
