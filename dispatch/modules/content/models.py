from django.db.models import (
    Model, DateTimeField, CharField, TextField, PositiveIntegerField,
    ImageField, BooleanField, ForeignKey, OneToOneField, ManyToManyField, SlugField, SET_NULL, Manager, permalink)

from django.core.validators import MaxValueValidator
from django.conf import settings
from django.template import loader, Context

from dispatch.apps.core.models import Person

from dispatch.apps.frontend.models import Script, Snippet, Stylesheet

from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image as Img
import StringIO, json, os, re

from django.db.models.signals import post_delete
from django.dispatch import receiver


class Tag(Model):
    name = CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name

class Topic(Model):
    name = CharField(max_length=255)

    def __str__(self):
        return self.name

class Resource(Model):
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    authors = ManyToManyField(Person, through="Author", blank=True, null=True)

    def save_authors(self, authors):
        Author.objects.filter(resource_id=self.id).delete()
        n=0
        if type(authors) is not list:
            authors = authors.split(",")
        for author in authors:
            try:
                person = Person.objects.get(id=author)
                Author.objects.create(resource=self,person=person,order=n)
                n = n + 1
            except Person.DoesNotExist:
                pass

class Section(Model):
    name = CharField(max_length=100, unique=True)
    slug = SlugField(unique=True)

    def __str__(self):
        return self.name

class Publishable(Model):

    parent = ForeignKey(Resource, related_name='parent', blank=True, null=True)
    preview = BooleanField(default=False)
    revision_id = PositiveIntegerField(default=0)
    head = BooleanField(default=False)

    # Overriding
    def save(self, revision=True, *args, **kwargs):

        if revision:
            self.head = True
            self.revision_id += 1

            if self.parent:
                Article.objects.filter(parent=self.parent,head=True).update(head=False)
                # Both fields required for this to work -- something to do with model inheritance.
                self.pk = None
                self.id = None

        super(Publishable, self).save(*args, **kwargs)

        if not self.parent:
            self.parent = self
            super(Publishable, self).save(update_fields=['parent'])

        return self

    def get_previous_revision(self):
        if self.parent == self:
            return self
        revision = Article.objects.filter(parent=self.parent).order_by('-pk')[1]
        return revision

    def check_stale(self):
        if self.revision_id == 0:
            return (False, self)
        head = Article.objects.get(parent=self.parent, head=True)
        return (head.revision_id != self.revision_id, head)

    class Meta:
        abstract = True

class ArticleManager(Manager):

    def get(self, *args, **kwargs):
        if 'pk' in kwargs:
            kwargs['parent'] = kwargs['pk']
            kwargs['head'] = True
            del kwargs['pk']
        return super(ArticleManager, self).get(*args, **kwargs)

    def get_revision(self, *args, **kwargs):
        return super(ArticleManager, self).get(*args, **kwargs)

    def get_frontpage(self, reading_times=None, section=None, section_id=None):

        if reading_times is None:
            reading_times = {
                'morning_start': '11:00:00',
                'midday_start': '11:00:00',
                'midday_end': '16:00:00',
                'evening_start': '16:00:00',
            }

        context = {
            'section': section,
            'section_id': section_id,
        }

        context.update(reading_times)

        if section is not None:
            query = """
            SELECT *,
                TIMESTAMPDIFF(SECOND, published_at, NOW()) as age,
                CASE reading_time
                     WHEN 'morning' THEN IF( CURTIME() < %(morning_start)s, 1, 0 )
                     WHEN 'midday'  THEN IF( CURTIME() >= %(midday_start)s AND CURTIME() < %(midday_end)s, 1, 0 )
                     WHEN 'evening' THEN IF( CURTIME() >= %(evening_start)s, 1, 0 )
                     ELSE 0.5
                END as reading
                FROM content_article
                INNER JOIN content_section on content_article.section_id = content_section.id AND content_section.slug = %(section)s
                WHERE head = 1
                ORDER BY reading DESC, ( age * ( 1 / ( 4 * importance ) ) ) ASC
                LIMIT 7
            """
        elif section_id is not None:
            query = """
            SELECT *,
                TIMESTAMPDIFF(SECOND, published_at, NOW()) as age,
                CASE reading_time
                     WHEN 'morning' THEN IF( CURTIME() < %(morning_start)s, 1, 0 )
                     WHEN 'midday'  THEN IF( CURTIME() >= %(midday_start)s AND CURTIME() < %(midday_end)s, 1, 0 )
                     WHEN 'evening' THEN IF( CURTIME() >= %(evening_start)s, 1, 0 )
                     ELSE 0.5
                END as reading
                FROM content_article
                WHERE head = 1 AND section_id = %(section_id)s
                ORDER BY reading DESC, ( age * ( 1 / ( 4 * importance ) ) ) ASC
                LIMIT 7
            """
        else:
            query = """
            SELECT *,
                TIMESTAMPDIFF(SECOND, published_at, NOW()) as age,
                CASE reading_time
                     WHEN 'morning' THEN IF( CURTIME() < %(morning_start)s, 1, 0 )
                     WHEN 'midday'  THEN IF( CURTIME() >= %(midday_start)s AND CURTIME() < %(midday_end)s, 1, 0 )
                     WHEN 'evening' THEN IF( CURTIME() >= %(evening_start)s, 1, 0 )
                     ELSE 0.5
                END as reading
                FROM content_article
                WHERE head = 1
                ORDER BY reading DESC, ( age * ( 1 / ( 4 * importance ) ) ) ASC
                LIMIT 7
            """

        return self.raw(query, context)

    def get_sections(self, exclude=False, frontpage=[]):

        results = {}

        sections = Section.objects.all()

        for section in sections:
            articles = self.exclude(id__in=frontpage).filter(section=section,head=True)[:5]
            if len(articles) > 0:
                results[section.slug] = {
                    'first': articles[0],
                    'stacked': articles[1:3],
                    'bullets': articles[3:],
                    'rest': articles[1:4],
                }

        return results

    def get_most_popular(self, count=10):
        return self.filter(head=True).order_by('-importance')[:count]

class Article(Resource, Publishable):
    long_headline = CharField(max_length=200)
    short_headline = CharField(max_length=100)
    section = ForeignKey('Section')

    is_active = BooleanField(default=True)
    is_published = BooleanField(default=False)
    published_at = DateTimeField()
    slug = SlugField()

    topics = ManyToManyField('Topic', blank=True, null=True)
    tags = ManyToManyField('Tag', blank=True, null=True)
    shares = PositiveIntegerField(default=0, blank=True, null=True)

    IMPORTANCE_CHOICES = [(i,i) for i in range(1,6)]

    importance = PositiveIntegerField(validators=[MaxValueValidator(5)], choices=IMPORTANCE_CHOICES, default=3)

    READING_CHOICES = (
        ('anytime', 'Anytime'),
        ('morning', 'Morning'),
        ('midday', 'Midday'),
        ('evening', 'Evening'),
    )

    reading_time = CharField(max_length=100, choices=READING_CHOICES, default='anytime')

    featured_image = ForeignKey('ImageAttachment', related_name="featured_image", blank=True, null=True)

    images = ManyToManyField("Image", through='ImageAttachment', related_name='images', blank=True, null=True)
    videos = ManyToManyField('Video', blank=True, null=True)

    scripts = ManyToManyField(Script, related_name='scripts', blank=True, null=True)
    stylesheets = ManyToManyField(Stylesheet, related_name='stylesheets', blank=True, null=True)
    snippets = ManyToManyField(Snippet, related_name='snippets', blank=True, null=True)

    content = TextField()
    snippet = TextField()

    objects = ArticleManager()

    def __str__(self):
        return self.long_headline

    def tags_list(self):
        return ",".join(self.tags.values_list('name', flat=True))

    def topics_list(self):
        return ",".join(self.topics.values_list('name', flat=True))

    def images_list(self):
        return ",".join([str(i) for i in self.images.values_list('id', flat=True)])

    def get_authors(self):
        return self.authors.order_by('author__order')

    def authors_list(self):
        return ",".join([str(i) for i in self.get_authors().values_list('id', flat=True)])

    def get_date(self):
        return self.published_at.strftime("%Y-%m-%d")

    def get_time(self):
        return self.published_at.strftime("%H:%M")

    def save_related(self, data):
        tags = data["tags-list"]
        topics = data["topics-list"]
        authors = data["authors-list"]
        if tags:
            self.save_tags(tags)
        if topics:
            self.save_topics(topics)
        if authors:
            self.save_authors(authors)

    def save_tags(self, tags):
        self.tags.clear()
        for tag in tags.split(","):
            try:
                ins = Tag.objects.get(name=tag)
            except Tag.DoesNotExist:
                ins = Tag.objects.create(name=tag)
            self.tags.add(ins)

    def save_topics(self, topics):
        self.topics.clear()
        for topic in topics.split(","):
            try:
                ins = Topic.objects.get(name=topic)
            except Topic.DoesNotExist:
                ins = Topic.objects.create(name=topic)
            self.topics.add(ins)

    def get_json(self):
        try:
            return json.loads(self.content)
        except ValueError:
            return self.content

    def get_html(self):
        nodes = json.loads(self.content)
        html = ""
        for node in nodes:
            if type(node) is dict:
                template = loader.get_template("image.html")
                id = node['data']['attachment_id']
                attach = ImageAttachment.objects.get(id=id)
                c = Context({
                    'id': attach.id,
                    'src': attach.image.get_absolute_url(),
                    'caption': attach.caption,
                    'credit': ""
                })
                html += template.render(c)
            else:
                html += "<p>%s</p>" % node
        return html

    def save_attachments(self):
        nodes = json.loads(self.content)
        for node in nodes:
            if type(node) is dict and node['type'] == 'image':
                image_id = node['data']['images'][0]['id']
                image = Image.objects.get(id=image_id)
                if node['data']['attachment_id']:
                    attachment = ImageAttachment.objects.get(id=node['data']['attachment_id'])
                else:
                    attachment = ImageAttachment()
                attachment.caption = node['data']['caption']
                attachment.image = image
                attachment.article = self
                attachment.save()
                del node['data']
                node['data'] = {
                    'attachment_id': attachment.id
                }
        self.content = json.dumps(nodes)

    def save_new_attachments(self, attachments):
        def save_new_attachment(code):
            args = re.findall(r'(\".+\"|[0-9]+)+', code.group(0))
            temp_id = int(args[0])
            return "[image %s]" % str(attachments[temp_id].id)
        self.content = re.sub(r'\[temp_image[^\[\]]*\]', save_new_attachment, self.content)

    def clear_old_attachments(self):

        previous_attachments = ImageAttachment.objects.filter(article=self.get_previous_revision())

        def keep_attachment(code):
            args = re.findall(r'(\".+\"|[0-9]+)+', code.group(0))
            id = int(args[0])
            try:
                ins = previous_attachments.get(pk=id)
                ins.article = self
                ins.pk = None
                ins.save()
                return "[image %s]" % str(ins.id)
            except ImageAttachment.DoesNotExist:
                return code.group(0)
        self.content = re.sub(r'\[image[^\[\]]*\]', keep_attachment, self.content)

    def get_author_string(self):
        author_str = ""
        authors = self.authors.order_by('author__order')
        n = 1
        for author in authors:
            if n + 1 == len(authors) and len(authors) > 0:
                author_str = author_str + author.full_name + " and "
            elif n == len(authors):
                author_str = author_str + author.full_name
            else:
                author_str = author_str + author.full_name + ", "
            n = n + 1
        return author_str

    def get_absolute_url(self):
        return "http://localhost:8000/%s/%s/" % (self.section.name.lower(), self.slug)

    def get_admin_url(self):
        return ('dispatch.apps.manager.views.article_edit', [str(self.parent.id)])

    get_admin_url = permalink(get_admin_url)

class Author(Model):
    resource = ForeignKey(Resource)
    person = ForeignKey(Person)
    order = PositiveIntegerField()

class Video(Resource):
    title = CharField(max_length=255)
    url = CharField(max_length=500)

class Image(Resource):
    img = ImageField(upload_to='images')
    title = CharField(max_length=255, blank=True, null=True)

    ROOT_URL = settings.MEDIA_URL
    #ROOT_URL = "http://petersiemens.com/dispatch/media/"

    SIZES = {
        'large': (1600, 900),
        'medium': (800, 600),
        'square': (250, 250)
    }

    THUMBNAIL_SIZE = 'square'

    def filename(self):
        return os.path.basename(self.img.name)

    def get_name(self):
        return re.split('.(jpg|gif|png)', self.img.name)[0]

    def get_absolute_url(self):
        return self.ROOT_URL + str(self.img)

    def get_medium_url(self):
        return self.ROOT_URL+"%s-%s.jpg" % (self.get_name(), 'medium')

    def get_thumbnail_url(self):
        return self.ROOT_URL+"%s-%s.jpg" % (self.get_name(), self.THUMBNAIL_SIZE)

    def get_wide_url(self):
        return self.ROOT_URL+"%s-%s.jpg" % (self.get_name(), 'wide')

    #Overriding
    def save(self, *args, **kwargs):
        super(Image, self).save(*args, **kwargs)
        if self.img:
            image = Img.open(StringIO.StringIO(self.img.read()))
            name = re.split('.(jpg|gif|png)', self.img.name)[0]

            for size in self.SIZES.keys():
                self.save_thumbnail(image, self.SIZES[size], name, size)


    def save_thumbnail(self, image, size, name, label):
        width, height = size
        (imw, imh) = image.size
        if (imw > width) or (imh > height) :
            image.thumbnail(size, Img.ANTIALIAS)
        name = "%s-%s.jpg" % (name, label)
        output = os.path.join(settings.MEDIA_ROOT, name)
        image.save(output, format='JPEG', quality=75)

    @receiver(post_delete)
    def delete_images(sender, instance, **kwargs):
        if sender == Image:
            name = instance.img.name.split('.')[0]

            # Delete original
            path = os.path.join(settings.MEDIA_ROOT, instance.img.name)
            try:
                os.remove(path)
            except OSError:
                pass

            # Delete other sizes
            for size in sender.SIZES.keys():
                filename = name + "-%s.jpg" % size
                path = os.path.join(settings.MEDIA_ROOT, filename)
                try:
                    os.remove(path)
                except OSError:
                    pass

class Gallery(Resource):
    #images = ManyToManyField('Image', through="ImageAttachment", blank=True, null=True)
    pass

class ImageAttachment(Model):
    NORMAL = 'normal'
    FILE = 'file'
    COURTESY = 'courtesy'
    TYPE_CHOICES = (
        (NORMAL, 'Normal'),
        (FILE, 'File photo'),
        (COURTESY, 'Courtesy photo'),
    )

    article = ForeignKey(Article, blank=True, null=True)
    caption = CharField(max_length=255, blank=True, null=True)
    image = ForeignKey(Image, related_name='image', on_delete=SET_NULL, null=True)
    type = CharField(max_length=255, choices=TYPE_CHOICES, default=NORMAL, null=True)