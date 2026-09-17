# QA хэрэгслүүд

Dashboard-ийн **QA хэрэгслүүд** холбоосоор Test/Pre харьцуулалт, smoke test,
үйлдлийн түүх нээгдэнэ. Pod-ийн **Лог / Дэлгэрэнгүй** холбоосоор хувилбар,
container-ийн төлөв, events, одоогийн/өмнөх лог, bug report нээгдэнэ.
Хэрэглэгчийн нэвтрэлт болон эрхийн түвшин нэмээгүй.

## Ашиглалтад оруулах

1. Шинэ image build/push хийгээд `manifests/deploy.yaml` дахь image tag-ийг
   шинэчилж, одоогийн release процессоор deploy хийнэ. Image tag-ийг build
   хийгээгүй байхад энэ өөрчлөлт автоматаар солиогүй.
2. Доорх audit хүснэгтийг нэг удаа үүсгэнэ.
3. Шалгах API URL-уудаа `config/smoke-tests.json`-д тохируулна. Файл image-д
   ордог тул build хийхээсээ өмнө засах, эсвэл ConfigMap-ээр mount хийж
   `QA_SMOKE_CONFIG` орчны хувьсагчаар файлын замыг заах боломжтой.
4. Events унших эрхийг хоёр namespace-д шалгана. Дутуу бол
   `manifests/rbac/qa-events.yaml`-ийг тусад нь apply хийнэ. Parent kustomization-д
   нэмэхгүй: namespace нь `pre-new-ms` болж өөрчлөгдөнө.

```sh
oc auth can-i list events -n test-ms --as=system:serviceaccount:pre-new-ms:qa-support-sa
oc auth can-i list events -n pre-new-ms --as=system:serviceaccount:pre-new-ms:qa-support-sa
# Only if permission is missing:
oc apply -f manifests/rbac/qa-events.yaml
```

Deployment/ReplicaSet/Pod get/list болон pods/log get эрх өмнөх байдлаар хэрэгтэй.
Апп өөрөө RBAC, Job, Pod, PVC, database үүсгэхгүй. `/healthz` нь dashboard
процессийн HTTP health endpoint бөгөөд бусад сервисийн эрүүл төлөвийг батлахгүй.

## Үйлдлийн түүх — PostgreSQL

Анхдагчаар одоогийн `QA_PRE_DB_*` холболтыг ашиглана. Audit-д тусдаа холболт
ашиглах бол `QA_AUDIT_DB_HOST`, `PORT`, `NAME`, `USER`, `PASSWORD`-ийг ижил
prefix-тэйгээр Secret-д тохируулна. Дутуу утга Pre DB тохиргооноос авна.
Нууц үгийг repository-д бичихгүй.

DB админ `sql/audit.sql`-ийг зорилтот DB дээр нэг удаа ажиллуулах эсвэл шинэ
image deploy хийсний дараа дараах CLI-г schema үүсгэх эрхтэй холболтоор ажиллуулна:

```sh
oc exec -n pre-new-ms deployment/qa-support-web-pod -- flask --app app init-audit-db
```

Зөвхөн `qa_support` schema болон `action_history` хүснэгт/index-үүдийг
`IF NOT EXISTS` нөхцөлөөр үүсгэнэ. Одоо байгаа бизнес хүснэгтүүдийг өөрчлөхгүй.
Аппын runtime DB хэрэглэгчид `qa_support` schema USAGE,
`qa_support.action_history` хүснэгт SELECT/INSERT эрх хангалттай. DBA өөр
хэрэглэгчээр хүснэгт үүсгэсэн бол эдгээр эрхийг тусад нь олгоно.

Rollout restart хүсэлтийн амжилт/хаагдсан/алдаатай үр дүн, Email/SMS-ийн
орчин тус бүрийн үр дүн, smoke test үр дүнг хадгална. Email, утас, API token,
логийн агуулга хадгалахгүй. Нэвтрэлтгүй тул хэрэглэгчийн нэр/IP-г хэн хийснийг
нотлох баримт гэж бүртгэхгүй. HTTP хүсэлт дууссаны дараах бүртгэл тул process
гэнэт тасарвал тухайн бичлэг дутуу үлдэх боломжтой; энэ нь cluster audit log биш.
DB алдаа гарвал үндсэн үйлдлийн бодит үр дүнг хэвээр буцааж,
`audit_warning`-ийг UI-д харуулна. DB бэлэн болохоос өмнөх үйлдлийг нөхөж бичихгүй.
Түүхийг устгах автомат retention оруулаагүй; хадгалах хугацааг DBA-тай тогтооно.

## Smoke test

Deployment readiness шалгалт хоёр орчинд шууд бэлэн. Бизнес API шалгахын тулд
баталгаатай URL-уудаа дараах хэлбэрээр тохируулна (доорх нь жишээ):

```json
{
  "test-ms": [
    {
      "id": "service-health",
      "name": "Service health",
      "url": "https://YOUR-TEST-SERVICE/health",
      "expected_status": 200,
      "body_contains": "UP"
    }
  ],
  "pre-new-ms": []
}
```

Нэг орчинд дээд тал нь 10 HTTP шалгалт. Зөвхөн серверт тохируулсан URL-д GET
илгээнэ; browser-оос дурын URL, method, header дамжуулахгүй. Хүлээгдэх status
болон хүсвэл эхний 64 KiB дотор `body_contains` шалгана. Тус бүр 5 секундийн
socket timeout, дээд тал нь 4 зэрэг холболт. Redirect дагахгүй, TLS баталгаажуулна.
Proxy орчны хувьсагч ашиглахгүй; dashboard pod-оос URL-д шууд сүлжээний гарц
байх шаардлагатай. NetworkPolicy хориглосон бол тухайн гарцыг нээнэ.

Bearer token хэрэгтэй бол `bearer_token_env`-д token хадгалсан Secret env-ийн
нэрийг заана; зөвхөн HTTPS-д ашиглана. Дотоод CA хэрэгтэй бол сертификатыг mount
хийгээд `QA_SMOKE_CA_FILE`-д замыг өгнө. URL/token/body-г түүхэнд хадгалахгүй.
Response body-г UI-д буцаахгүй. PASS/FAIL нь зөвхөн тохируулсан шалгалтын хүрээнд.

## Хувилбар, лог, report

- Actual pod image болон image digest, хүссэн deployment image-ийг тусад нь харуулна.
- CI/CD pipeline нь pod template annotations-д `qa.m-bank.mn/build-number`,
  `qa.m-bank.mn/deployed-at` (UTC RFC3339) бичвэл build/deploy огноо харагдана.
  Мэдээлэлгүй бол “Бүртгээгүй”; Deployment creationTimestamp-ийг deploy огноо гэж
  орлуулахгүй. Revision ба restartedAt нь тусдаа утга.
- Орчны харьцуулалт Deployment нэрээр тааруулж image/container mapping, build,
  replica болон төлөвийг харьцуулна. Өөр нэртэй сервисүүдийг автоматаар таахгүй.
- Логийн нэг хүсэлт дээд тал нь 5000 мөр, 1 MiB. Хугацаа нь сүүлийн 5/15 минут,
  1/6/24 цаг эсвэл хадгалагдсан бүх хугацаа; текст/түвшний хайлт зөвхөн татсан мөрд
  үйлчилнэ. Multi-line stack trace-ийн мөрүүд тусдаа шүүгдэнэ.
- `--previous` нь одоогийн pod-ийн хамгийн сүүлд дууссан container-ийн лог.
  Устсан pod, олон хоногийн архивт төвлөрсөн логийн систем шаардлагатай.
- Events нь pod UID-аар шүүгдэнэ; cluster-ийн event retention-оос шалтгаалж
  хуучин event харагдахгүй байж болно.
- Bug report шинэ snapshot татаж, сонгосон container/хугацаа/текст/түвшний шүүлтүүрийг
  хэрэглэнэ. Хэрэглэгч report-оо засаж, хуулж эсвэл Markdown файл татна.
  Tracker руу автоматаар нийтлэхгүй. Pod spec-ийн env/Secret мэдээлэл report-д орохгүй;
  харин application log өөрөө мэдрэмтгий мэдээлэл агуулж болох тул хавсаргахаас өмнө шалгана.

## Шалгалт

```sh
python -m unittest discover -s tests -v
node --test tests/dashboard.test.cjs tests/qa.test.cjs
```

Tests нь cluster/HTTP/DB холболтыг mock хийдэг; бодит workload restart хийхгүй.
