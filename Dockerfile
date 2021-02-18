# Install Rasa
FROM python:3.6
ENV PIP_DEFAULT_TIMEOUT 100
RUN pip install --upgrade pip && \
    pip install rasa==1.10.11 && \
    pip install spacy==2.3.0 && \
    pip install pydub==0.24.1 && \
    spacy download es_core_news_md && \
    spacy link es_core_news_md es

# Install other requirements and add files
ADD requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

# Install Caddy
USER root
RUN yum -y install libcap nss-tools wget tar && \
    wget https://github.com/caddyserver/caddy/releases/download/v2.0.0/caddy_2.0.0_linux_amd64.tar.gz && \
    tar -C /usr/local/bin/ -xf caddy_2.0.0_linux_amd64.tar.gz caddy && \
    chmod a+x entrypoint.sh

RUN yum localinstall -y --nogpgcheck https://download1.rpmfusion.org/free/el/rpmfusion-free-release-7.noarch.rpm https://download1.rpmfusion.org/nonfree/el/rpmfusion-nonfree-release-7.noarch.rpm

RUN yum install ffmpeg -y

# ImageMagick Use dcraw Instead of ufraw
RUN sed -iE 's/.*ufraw.*/  \<delegate decode="dng:decode" stealth="True" command="\&quot;dcraw\&quot; -cw \&qout;%i\&qout; \&gt; \&quot;%u.ppm\&quot;"\/>/g' /etc/ImageMagick/delegates.xml

RUN LD_LIBRARY_PATH=/opt/rh/rh-python35/root/usr/lib64 /opt/rh/rh-python35/root/usr/bin/pip install -U pip
# Copy the S2I scripts from the specific language image to $STI_SCRIPTS_PATH.
COPY ./s2i/bin/ $STI_SCRIPTS_PATH

# Each language image can have 'contrib' a directory with extra files needed to
# run and build the applications.
COPY ./contrib/ /opt/app-root

# In order to drop the root user, we have to make some directories world
# writable as OpenShift default security model is to run the container under
# random UID.
RUN chown -R 1001:0 /opt/app-root && chmod -R og+rwx /opt/app-root

USER 1001

# Set the default CMD to print the usage of the language image.
CMD $STI_SCRIPTS_PATH/usage
