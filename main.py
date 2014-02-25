#!/usr/bin/python
# -*- coding: utf-8 -*-
from ConfigParser import SafeConfigParser
import codecs
import cherrypy

from feedgen.feed import FeedGenerator

import imaplib, email
from email.header import decode_header
import HTMLParser

class EmailClient(object):
	def decode_email(self,string,idx=0):
		cherrypy.log(str(decode_header(string)))
		if idx < len(decode_header(string)):
			if decode_header(string)[idx][1] is not None:
				return decode_header(string)[idx][0].decode(decode_header(string)[idx][1])
			else:
				return decode_header(string)[idx][0]
		else:
			return ""

	def __init__(self):
		self.mailserver = imaplib.IMAP4_SSL(config.get('imap','server'))
		self.mailserver.login(config.get('imap','username'), config.get('imap','password'))
		self.mailbox = config.get('imap','mailbox')

	def listBox(self,mailbox="INBOX"):
		import datetime
		self.mailbox = mailbox
		self.mailserver.select(self.mailbox)
		date = (datetime.date.today() - datetime.timedelta(config.getint('imap','lastdays'))).strftime("%d-%b-%Y")
		typ, msgnums = self.mailserver.uid('search', None, "(SENTSINCE {date})".format(date=date))
		#TODO check typ

		return msgnums[0].split()

	def _getBody(self, mail, typ='text/html'):
		charset = mail.get_content_charset()

		if mail.is_multipart():
			for part in mail.get_payload():
				body, t = self._getBody(part)
				if body != None:
					return body, t
		elif mail.get_content_type() == typ:
			return unicode(mail.get_payload(decode=True), charset, 'ignore'), mail.get_content_type()
		elif mail.get_content_type() == 'text/plain':
			return '<pre>'+unicode(mail.get_payload(decode=True), charset, 'ignore')+'</pre>', mail.get_content_type()
		else:
			return None, None

	def _getAttachment(self, mail, cid):
		if mail.is_multipart():
			for part in mail.get_payload():
				attach, typ = self._getAttachment(part, cid)
				if attach != None:
					return attach, typ
		elif mail.get('Content-ID'):
			if mail.get('Content-ID')[1:-1] == cid:  ## Remove <> from cid
				return mail.get_payload(decode=True), mail.get_content_type()
		return None, None

	def cid_2_images(self,body,msgn):
		'''this replaces the <img src="<cid:SOMETHING>"/> tags with <img src="SOME URL"/> tags in the message'''
		from BeautifulSoup import BeautifulSoup
		import re

		soup = BeautifulSoup(body)
		images_with_cid = soup('img', attrs = {'src' : re.compile('cid:.*')})
		for image_tag in images_with_cid:
			image_tag['src'] = config.get("main","baseurl")+'attach/'+msgn+'/'+image_tag['src'][4:]
			image_tag['style'] = "max-width: 100%; max-height: 100%;"
		return soup.renderContents().decode('utf-8')


	def getImage(self, msgn, cid):
		self.mailserver.select(self.mailbox)
		typ, data = self.mailserver.uid('fetch', msgn, '(RFC822)')

		mail = email.message_from_string(data[0][1])

		try:
			attach, typ = self._getAttachment(mail, cid)
		except TypeError as e:
			cherrypy.log.error("getImage(): "+str(e))
			raise cherrypy.NotFound()

		return attach, typ

	def getEMail(self, msgn):
		self.mailserver.select(self.mailbox)
		typ, data = self.mailserver.uid('fetch', msgn, '(RFC822)')

		mail = email.message_from_string(data[0][1])


		from_name = self.decode_email(mail['From'],0)
		from_email = self.decode_email(mail['From'],1)
		subject = self.decode_email(mail['subject'])
		date = mail['Date']

		if from_email is None:
			from_email = config.get("imap","default-from")

		body, ctype = self._getBody(mail)
		if ctype == "text/html":
			body = self.cid_2_images(body, msgn)

		return {"subject": subject, "From": {'name': from_name, 'email': from_email}, "date": date, "body": body}

class AttachReader(object):
	exposed = True
	def GET(self, uid, cid):
		cherrypy.log("AttachReader "+uid + "	" + cid)
		client = EmailClient()
		attach, typ = client.getImage(uid, cid)
		cherrypy.response.headers['Content-Type'] = typ
		return attach

class EmailReader(object):
	exposed = True

	def GET(self, uid=None, utm_medium=None, utm_source=None):
		from BeautifulSoup import BeautifulSoup, Tag, NavigableString
		cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
		cherrypy.log("EmailReader "+uid)
		if uid==None:
			return "404 Not Found"
		client = EmailClient()
		mail = client.getEMail(uid)

		if mail['body'] != None:
			soup = BeautifulSoup(mail['body'])
		else:
			soup = BeautifulSoup('<html><body><h1>'+mail['subject']+'</h1></body></html>')

		subject = NavigableString(mail['subject'])
		title = Tag(soup, "title")
		title.insert(0, subject)


		if soup.find('head') == None:
			head = Tag(soup, "head")
			html = Tag(soup, "html")
			html.insert(0, head)
			body = Tag(soup, "body")
			body.insert(0, soup)
			html.insert(0, body)
		else:
			head = soup.html.head
			html = soup

		# Redirecting
		# We might want to lookup the content through a different URL
		if config.has_option('main','redirecturl') and config.has_option('main','redirectdomain'):
			cherrypy.log("Adding Redirects")
			meta = Tag(soup, "meta")
			meta['http-equiv'] = "refresh"
			meta['content'] = "0;url="+config.get('main','redirecturl')+"?"+uid
			link = Tag(soup, "link")
			link['rel'] = "canonical"
			link['href'] = config.get('main','redirecturl')+"?"+uid
			js = Tag(soup, "script")
			js['type'] = "text/javascript"
			script = NavigableString("if(document.domain != '"+config.get('main','redirectdomain')+"') window.location.href ='" + config.get('main','redirecturl')+"?"+uid+"'")
			js.insert(0, script)

			head.insert(0, title)
			head.insert(0, link)
			head.insert(0, js)

		return html.renderContents().decode('utf-8');

class RSSClient(object):
	exposed = True
	def GET(self):
		cherrypy.response.headers["Access-Control-Allow-Origin"] = "*"
		fg = FeedGenerator()
		#TODO create icon
		# fg.icon('http://www.det.ua.pt')
		fg.id(config.get('rss','id'))
		fg.title(config.get('rss','title'))
		fg.subtitle(config.get('rss','subtitle'))
		fg.description(config.get('rss','description'))
		fg.author({'name': config.get('rss','author_name'), 'email':config.get('rss','author_email')})
		fg.language(config.get('rss','language'))
		fg.link(href=config.get('rss','href'), rel='related')

		client = EmailClient()

		for msgn in reversed(client.listBox(config.get('imap','mailbox'))[:config.getint('rss','maxitems')]):
			cherrypy.log("RSS Entry: "+msgn)
			em = client.getEMail(msgn)
			entry = fg.add_entry()
			entry.title(em['subject'])
			entry.author({'name': em['From']['name'], 'email': em['From']['email']})
			entry.guid(config.get("main","baseurl")+'news/'+msgn)
			entry.link({'href':config.get("main","baseurl")+'news/'+msgn, 'rel':'alternate'})
			entry.pubdate(em['date'])
			entry.content(em['body'])
		return	fg.rss_str(pretty=True)

config = SafeConfigParser()
# Open the file with the correct encoding
with codecs.open('config.ini', 'r', encoding='utf-8') as f:
    config.readfp(f)

root = RSSClient();
root.news = EmailReader();
root.attach = AttachReader();

conf = {
	'global': {
		'server.socket_host': '0.0.0.0',
		'server.socket_port': config.getint('main', 'port'),
	},
	'/': {
		'log.access_file': '/var/log/imap2rss.access.log',
		'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
	}
}
if config.getboolean('main', 'daemon'):
	cherrypy.process.plugins.Daemonizer(cherrypy.engine).subscribe()

cherrypy.quickstart(root, '/', conf)
