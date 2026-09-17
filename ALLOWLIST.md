# Email / SMS allowlist

`/allowlist` цонхыг үндсэн самбарын **Email / SMS жагсаалт** товчоор нээнэ.

- Email, 8 оронтой утасны дугаарын аль нэгийг эсвэл хоёуланг оруулна.
- Нэмэх Test / Pre орчноо сонгоно. Анх хоёулаа сонгогдсон байна.
- Орчин бүрд `нэмэгдсэн`, `өмнө бүртгэгдсэн`, эсвэл алдааны үр дүн гарна.
- Жагсаалтаас орчин, email/дугаарын төрлөөр сонгон хайна. Хуудас бүр 50 мөртэй.
- Email-ийн эхлэл/төгсгөлийн хоосон зайг арилгана; том/жижиг үсгийг өөрчлөхгүй.
- Нэг орчны email + phone нэг transaction; алдаа гарвал хоёул буцна.
  Test болон Pre тусдаа transaction тул нэг нь амжилттай, нөгөө нь алдаатай байж болно.
- Давхар хүсэлтийг table lock + шалгалтаар зохицуулна. Устгах үйлдэл байхгүй.

## Deploy

1. `manifests/db.env` contains the local DB settings and is excluded from Git.
   On a fresh checkout, copy `manifests/db.env.example` to `manifests/db.env`
   and fill both passwords. In CI, create this file from CI secrets.
2. Build/push the image and update the deployment image tag if code changed.
3. Run `oc apply -k manifests`. Kustomize creates the Secret, Deployment and
   Service together and updates the deployment's Secret reference automatically.
   Changed DB settings produce a new Secret hash and trigger a rollout on apply.
4. Check `oc rollout status deployment/qa-support-web-pod -n pre-new-ms`.

The Secret is required (`optional: false`). Missing configuration prevents pod
startup. `secret.example.yaml` is a manual reference only, not a Kustomize input.
Rendered manifests contain base64-encoded secrets: do not publish build output.
The pod requires network access to both databases on port 5432.
Existing internal access controls must also cover the allowlist page.
The application does not add its own login system.

The supplied INSERT syntax describes single-column tables. Column names are
read from database metadata. Multiple columns cause a configuration error;
this feature does not change the database schema.

## Local validation

```text
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests use mocked DB connections and do not add real email addresses or phone numbers.

