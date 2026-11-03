import base64
import binascii
import hashlib
import io
from PIL import Image, ImageOps, UnidentifiedImageError
from app.security import AppError,require

# A client-side crop is followed by independent server-side decoding/re-encoding.
Image.MAX_IMAGE_PIXELS = 1024*1024

def save(c,user_id,body):
    value=body.get('image')
    require(isinstance(value,str) and 0<len(value)<=280000,'头像文件为空或过大，请重新选择图片')
    try:
        raw=base64.b64decode(value,validate=True)
        require(0<len(raw)<=200000,'压缩后的头像不能超过200KB')
        with Image.open(io.BytesIO(raw)) as probe:
            require(probe.format in ['JPEG','PNG','WEBP'],'仅支持JPG、PNG或WebP图片')
            require(1<=probe.width<=1024 and 1<=probe.height<=1024,'头像尺寸过大，请重新选择图片')
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image=ImageOps.exif_transpose(image)
            if image.mode in ('RGBA','LA') or 'transparency' in image.info:
                image=image.convert('RGBA')
                background=Image.new('RGB',image.size,'#eef1e2')
                background.paste(image,mask=image.getchannel('A'))
                image=background
            else:
                image=image.convert('RGB')
            image=ImageOps.fit(image,(256,256),method=Image.Resampling.LANCZOS)
            out=io.BytesIO()
            image.save(out,format='JPEG',quality=85,optimize=True)
            result=out.getvalue()
    except AppError:
        raise
    except (ValueError,OSError,binascii.Error,UnidentifiedImageError,Image.DecompressionBombError):
        raise AppError('图片无法读取，请使用正常的JPG、PNG或WebP照片')
    version=hashlib.sha256(result).hexdigest()[:20]
    c.execute('UPDATE users SET avatar=?,avatar_version=? WHERE id=?',(result,version,user_id))
    c.execute("INSERT INTO audit(actor_id,action,target) VALUES(?,'profile.avatar',?)",(user_id,str(user_id)))
    return {'message':'头像已更新','avatar_version':version}
