# quejas-chatbot

Variables de entorno

| Nombre               | Descripción                                                                       | Ejemplo                                                                                |
|----------------------|-----------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| REDIS_HOSTNAME       | Host de Redis                                                                     | redis                                                                                  |
| REDIS_PASSWORD       | Contraseña de Redis                                                               | pass                                                                                   |
| END_TOKEN            | Token para terminar conversación                                                  | FIN                                                                                    |
| START_TOKEN          | Token para inicio de conversación                                                 | QUEJAS                                                                                 |
| SIMILARITY_THRESHOLD | Umbral de similitud de palabras para algoritmo de asociación de Estados (1 - 100) | 80                                                                                     |
| QUOTA                | Número de quejas permitidas por usuario al día                                    | 2                                                                                      |
| WHITELIST_API_URL    | API de lista blanca                                                               | http://172.16.162.62/apimobile-listablanca/v3/derechohabientes                         |
| VALIDITY_API_URL     | API de vigencia                                                                   | http://172.16.162.62/apimobile-vigencia/v3/derechohabientes                            |
| NSS_API_URL          | API de asegurados por NSS                                                         | http://serviciosdigitalesinterno-stage.imss.gob.mx/serviciosDigitales-rest/v1/personas |
| BENEFICIARY_API_URL  | API de derechohabientes                                                           | http://172.16.162.62/apimobile-derechohabiente/v3/derechohabientes                     |
| INBOX_URL            | API de inbox                                                                      | http://quejas-inbox:8080                                                               |
| CONNECTOR_URL        | API de conector                                                                   | http://quejas-chatbot-connector:8080/v1/mensaje/salidas                                |
