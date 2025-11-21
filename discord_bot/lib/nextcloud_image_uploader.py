import requests
import os
import xml.etree.ElementTree as ET
import logging

class NextcloudImageUploader:
    def __init__(self, nextcloud_url, username, password, upload_folder="bot_uploads"):
        self.nextcloud_url = nextcloud_url
        self.username = username
        self.password = password
        self.upload_folder = upload_folder.strip('/')  # Ensure no leading/trailing slashes
        self.upload_path = f"/remote.php/dav/files/{self.username}/{self.upload_folder}/"
        self.share_api_url = self.nextcloud_url + "/ocs/v1.php/apps/files_sharing/api/v1/shares"

    def upload_image(self, local_filepath):
        """Uploads an image from a local path to Nextcloud and returns the public URL."""
        filename = os.path.basename(local_filepath)
        upload_url = self.nextcloud_url + self.upload_path + filename
        auth = (self.username, self.password)
        headers = {'Content-Type': 'application/octet-stream'}

        try:
            with open(local_filepath, 'rb') as f:
                response = requests.put(upload_url, data=f, auth=auth, headers=headers)
                response.raise_for_status()
                logging.debug(f"File '{filename}' uploaded successfully to Nextcloud.")
                remote_file_path = f"/{self.upload_folder}/{filename}"
                return self._create_public_share_link(remote_file_path)

        except FileNotFoundError:
            logging.error(f"Error: Local file not found at '{local_filepath}'")
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Error uploading '{filename}' to Nextcloud: {e}")
            return None

    def _create_public_share_link(self, remote_path):
        """Creates a public share link for a file on Nextcloud."""
        auth = (self.username, self.password)
        headers = {'OCS-APIRequest': 'true', 'Content-Type': 'application/x-www-form-urlencoded'}
        data = {
            'path': remote_path,
            'shareType': 3,  # 3 for public link
            'permissions': 1,  # 1 for read-only
        }
        try:
            response = requests.post(self.share_api_url, data=data, auth=auth, headers=headers)
            response.raise_for_status()
            xml_content = response.text
            root = ET.fromstring(xml_content)
            share_url_element = root.find('.//url')
            if share_url_element is not None:
                share_url = share_url_element.text
                logging.debug(f"Public share link created for '{remote_path}': {share_url}")

                # https://cloud.hanime.zip/s/iqf8owR2W55sEFx
                share_url_id = share_url.split("/")[-1]
                share_url = f"https://cloud.hanime.zip/index.php/s/{share_url_id}/download"
                # https://cloud.hanime.zip/index.php/s/iqf8owR2W55sEFx/download

                return share_url
            else:
                logging.error(f"Error: Could not find the share URL in the response for '{remote_path}'.")
                logging.error(xml_content)
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Error creating share link for '{remote_path}': {e}")
            return None