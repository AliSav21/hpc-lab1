### готовність
`GET /healthz`
→ **200**
```json
{"status":"ok"}
```

### створення
`POST /donor-registry`
```json
{"donor_code": "D-1791320022", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **201**
```json
{"id":103,"donor_code":"D-1791320022","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### 400: вік 10 років
`POST /donor-registry`
```json
{"donor_code": "D-1791320025", "birth_year": 2016, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 2016 gives age 10; age must be between 18 and 55"}]}
```

### 400: вік 70 років
`POST /donor-registry`
```json
{"donor_code": "D-1791320025", "birth_year": 1956, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1956 gives age 70; age must be between 18 and 55"}]}
```

### 400: невалідна група крові
`POST /donor-registry`
```json
{"donor_code": "D-1791320025", "birth_year": 1996, "blood_group": "X", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"blood_group","message":"Input should be 'O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+' or 'AB-'"}]}
```

### 409: дубль donor_code
`POST /donor-registry`
```json
{"donor_code": "D-1791320022", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **409**
```json
{"errors":[{"field":"donor_code","message":"donor_code already exists"}]}
```

### перелік
`GET /donor-registry?limit=5`
→ **200**
```json
{"items":[{"id":1,"donor_code":"SEED-34e065-0000","birth_year":1978,"blood_group":"O-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-07-19","available":false},{"id":2,"donor_code":"SEED-34e065-0001","birth_year":2004,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2024-04-11","available":false},{"id":3,"donor_code":"SEED-34e065-0002","birth_year":1985,"blood_group":"B+","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-09-28","available":false},{"id":4,"donor_code":"SEED-34e065-0003","birth_year":1977,"blood_group":"A+","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-02-04","available":true},{"id":5,"donor_code":"SEED-34e065-0004","birth_year":1989,"blood_group":"AB+","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-12-08","available":true}],"total":103,"limit":5,"offset":0}
```

### перелік з фільтром available=false
`GET /donor-registry?available=false&limit=5`
→ **200**
```json
{"items":[{"id":1,"donor_code":"SEED-34e065-0000","birth_year":1978,"blood_group":"O-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-07-19","available":false},{"id":2,"donor_code":"SEED-34e065-0001","birth_year":2004,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2024-04-11","available":false},{"id":3,"donor_code":"SEED-34e065-0002","birth_year":1985,"blood_group":"B+","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2025-09-28","available":false},{"id":7,"donor_code":"SEED-34e065-0006","birth_year":2002,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2026-10-04","available":false},{"id":12,"donor_code":"SEED-34e065-0011","birth_year":2000,"blood_group":"A-","hla_typing":["A*02:01","B*07:02","DRB1*15:01"],"registered_on":"2026-03-26","available":false}],"total":35,"limit":5,"offset":0}
```

### 400: limit понад максимум
`GET /donor-registry?limit=1000`
→ **400**
```json
{"errors":[{"field":"limit","message":"Input should be less than or equal to 100"}]}
```

### за ідентифікатором
`GET /donor-registry/103`
→ **200**
```json
{"id":103,"donor_code":"D-1791320022","birth_year":1996,"blood_group":"O+","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":true}
```

### повна заміна
`PUT /donor-registry/103`
```json
{"donor_code": "D-1791320022", "birth_year": 1996, "blood_group": "AB-", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": false}
```
→ **200**
```json
{"id":103,"donor_code":"D-1791320022","birth_year":1996,"blood_group":"AB-","hla_typing":["A*02:01","B*07:02"],"registered_on":"2026-01-15","available":false}
```

### 400: PUT з невалідним віком
`PUT /donor-registry/103`
```json
{"donor_code": "D-1791320025", "birth_year": 1900, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **400**
```json
{"errors":[{"field":"birth_year","message":"birth_year 1900 gives age 126; age must be between 18 and 55"}]}
```

### 404: PUT неіснуючого
`PUT /donor-registry/999999999`
```json
{"donor_code": "D-1791320025", "birth_year": 1996, "blood_group": "O+", "hla_typing": ["A*02:01", "B*07:02"], "registered_on": "2026-01-15", "available": true}
```
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### видалення
`DELETE /donor-registry/103`
→ **204**
```json
(порожнє тіло)
```

### повторне видалення → 404
`DELETE /donor-registry/103`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```

### GET видаленого → 404
`GET /donor-registry/103`
→ **404**
```json
{"errors":[{"field":"id","message":"donor not found"}]}
```
