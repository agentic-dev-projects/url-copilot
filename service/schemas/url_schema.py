from pydantic import BaseModel

class QRResponseSchema(BaseModel):
    filename: str
    file_content: bytes
    media_type: str = "image/png"
