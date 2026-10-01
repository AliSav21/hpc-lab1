### готовність
`GET /healthz`
→ **200**
```json
{"status":"ok"}
```

### створення
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **201**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### 400: вік 10 років
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 2016, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 2016 gives age 10; age must be between 18 and 55"}]}
```

### 400: вік 70 років
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1956, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1956 gives age 70; age must be between 18 and 55"}]}
```

### 400: невалідна група крові
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "X", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"blood_group","message":"Input should be 'O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+' or 'AB-'"}]}
```

### 409: дубль donor_code
`POST /donor-registry`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **409**
```json
{"errors":[{"field":"donor_code","message":"donor_code already exists"}]}
```

### перелік
`GET /donor-registry?limit=5`
→ **200**
```json
{"items":[{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}],"total":1,"limit":5,"offset":0}
```

### перелік з фільтром available=false
`GET /donor-registry?available=false&limit=5`
→ **200**
```json
{"items":[],"total":0,"limit":5,"offset":0}
```

### 400: limit понад максимум
`GET /donor-registry?limit=1000`
→ **400**
```json
{"errors":[{"field":"limit","message":"Input should be less than or equal to 100"}]}
```

### за ідентифікатором
`GET /donor-registry/1`
→ **200**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### повна заміна
`PUT /donor-registry/1`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "AB-", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": false}
```
→ **200**
```json
{"id":1,"donor_code":"D-1790868490","birth_year":1996,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":false}
```

### 400: PUT з невалідним віком
`PUT /donor-registry/1`
```json
{"donor_code": "D-1790868490", "birth_year": 1900, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1900 gives age 126; age must be between 18 and 55"}]}
```

### 404: PUT неіснуючого
`PUT /donor-registry/999999999`
```json
{"donor_code": "D-1790868490", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### видалення
`DELETE /donor-registry/1`
→ **204**
```json
(порожнє тіло)
```

### повторне видалення → 404
`DELETE /donor-registry/1`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### GET видаленого → 404
`GET /donor-registry/1`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### запис перед down
`POST /donor-registry`
```json
{"donor_code": "PERSIST-1", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **201**
```json
{"id":3,"donor_code":"PERSIST-1","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### після down + up запис на місці (очікуємо 200)
`GET /donor-registry/3`
→ **200**
```json
{"id":3,"donor_code":"PERSIST-1","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### після down -v + up база порожня (total = 0)
`GET /donor-registry`
→ **200**
```json
{"items":[],"total":0,"limit":20,"offset":0}
```
