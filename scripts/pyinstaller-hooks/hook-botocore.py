"""Bundle this app's S3 models without unpacking every AWS service on each request."""
from PyInstaller.utils.hooks import collect_data_files

hiddenimports = ["html.parser"]
datas = collect_data_files("botocore", includes=[
    "cacert.pem", "data/s3/**", "data/_retry.json", "data/endpoints.json",
    "data/partitions.json", "data/sdk-default-configuration.json",
])
