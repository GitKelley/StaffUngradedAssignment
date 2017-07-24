"""
This XBlock defines a Staff Ungraded Assignment. Students are shown a description of the Assignment
and invited to upload a file which is not graded by staff.
"""

import datetime
import hashlib
import json
import logging
import mimetypes
import os
import pkg_resources
import pytz

from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import DateTime, Scope, String, Float, Integer
from xblock.fragment import Fragment
from xblockutils.studio_editable import StudioEditableXBlockMixin

from django.core.mail import EmailMessage
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import default_storage
from django.conf import settings
from django.template import Context, Template

#from student.models import user_by_anonymous_id

#from submissions.models import StudentItem as SubmissionsStudent

from webob.response import Response

#from .models import Mentor, MentorMessage

log = logging.getLogger(__name__)
BLOCK_SIZE = 8 * 1024

def reify(meth):
    """
    Decorator which caches value so it is only computed once.
    Keyword arguments:
    inst
    """
    def getter(inst):
        """
        Set value to meth name in dict and returns value.
        """
        value = meth(inst)
        inst.__dict__[meth.__name__] = value
        return value
    return property(getter)


class StaffUngradedAssignmentXBlock(XBlock, StudioEditableXBlockMixin):
    """
    An XBlock that allows users to submit assignments to staff
    """

    # Fields are defined on the class.  You can access them in your code as
    # self.<fieldname>.
    display_name = String(
        help="This name appears in the horizontal navigation at the top of the page.",
        default="Staff Ungraded Assignment",
        scope=Scope.settings
    )

    description = String(
        help="Text about the particular assignment",
        default=None,
        scope=Scope.content
    )

    assignment_name = String(
        help="Name of the assignment",
        default=None,
        scope=Scope.content
    )

    student_name = String(
        help="Name of the Student",
        default=None,
        scope=Scope.user_state
    )

    student_email = String(
        help="Email address of the Student",
        default=None,
        scope=Scope.user_state
    )

    annotated_filename = String(
        display_name="Annotated file name",
        scope=Scope.user_state,
        default=None,
        help="Name of the file uploaded"
    )

    annotated_mimetype = String(
        display_name="Mime type of annotated file",
        scope=Scope.user_state,
        default=None,
        help="The mimetype of the annotated file uploaded for this assignment."
    )

    annotated_timestamp = DateTime(
        display_name="Timestamp",
        scope=Scope.user_state,
        default=None,
        help="When the annotated file was uploaded")

    editable_fields = ["display_name", "description", "assignment_name" ]

    # TO-DO: change this view to display your data your own way.
    def student_view(self, context=None):
        """
        The primary view of the StaffUngradedAssignmentXBlock, shown to students
        when viewing courses.
        """

        html = self.resource_string("static/html/staffungradedassignment.html")
        frag = Fragment(html)
        frag.add_css(self.resource_string("static/css/staffungradedassignment.css"))
        frag.add_javascript(self.resource_string("static/js/src/staffungradedassignment.js"))
        frag.initialize_js('StaffUngradedAssignmentXBlock')
        return frag

    # def studio_view(self, context=None):
    #     """
    #     Return fragment for editing block in studio.
    #     """
    #     try:
    #         cls = type(self)
    #
    #         def none_to_empty(data):
    #             """
    #             Return empty string if data is None else return data.
    #             """
    #             return data if data is not None else ''
    #         edit_fields = (
    #             (field, none_to_empty(getattr(self, field.name)), validator)
    #             for field, validator in (
    #                 (cls.display_name, 'string'),
    #                 (cls.points, 'number'),
    #                 (cls.weight, 'number'))
    #         )
    #
    #         context = {
    #             'fields': edit_fields
    #         }
    #         fragment = Fragment()
    #         fragment.add_content(
    #             render_template(
    #                 'templates/staff_graded_assignment/edit.html',
    #                 context
    #             )
    #         )
    #         fragment.add_javascript(_resource("static/js/src/studio.js"))
    #         fragment.initialize_js('StaffGradedAssignmentXBlock')
    #         return fragment
    #     except:  # pragma: NO COVER
    #         log.error("Don't swallow my exceptions", exc_info=True)
    #         raise

    @XBlock.json_handler
    def save_sua(self, data, suffix=''):
        # pylint: disable=unused-argument
        """
        Persist block data when updating settings in studio.
        """
        self.display_name = data.get('display_name', self.display_name)

        # Validate points before saving
        points = data.get('points', self.points)
        # Check that we are an int
        try:
            points = int(points)
        except ValueError:
            raise JsonHandlerError(400, 'Points must be an integer')
        # Check that we are positive
        if points < 0:
            raise JsonHandlerError(400, 'Points must be a positive integer')
        self.points = points

        # Validate weight before saving
        weight = data.get('weight', self.weight)
        # Check that weight is a float.
        if weight:
            try:
                weight = float(weight)
            except ValueError:
                raise JsonHandlerError(400, 'Weight must be a decimal number')
            # Check that we are positive
            if weight < 0:
                raise JsonHandlerError(
                    400, 'Weight must be a positive decimal number'
                )
        self.weight = weight

    # This method allows the user to upload a filename
    @XBlock.handler
    def upload_assignment(self, request, suffix=''):
        """
        Save student's submission file.
        """
        require(self.upload_allowed())
        upload = request.FILES['file']
        student_id = self.student_submission_id()
        path = self._file_storage_path(sha1, upload.file.name)
        if not default_storage.exists(path):
            default_storage.save(path, File(upload.file))

    # This method handles sending the mail payload to the instructor
    def relay_message_to_email(self, request, mentor, course_key_string):
        """
        Relays the incoming message text from the client to email.
        """
        message = 'Created'
        content_type = ''
        status = 201

        try: #400 try and masked 500s
            attachment = request.FILES['file']
            email = EmailMessage(request.post['subject'], request.post['message'],
            request.user.email,
            [[email for email in [mentor.email1, mentor.email2, mentor.email3] if email]])
            email.attach(attachment.name, attachment.read(), attachment.content_type)
            email.send(fail_silently=False)

        except smtplib.SMTPRecipientsRefused, error: # masked 500
            # Errors in the relay of the message to email won't be
            # returned to the client. HTTP isn't appropriate for the
            # transfer or relay of email.
            LOGGER.info("RecipientsRefused '%s'", str(error))
            MentorMessage.objects.create( #pylint: disable=no-member
            subject=request.POST.get('subject', ''),
            message=request.POST.get('message', ''),
            course_id=course_key_string,
            is_delivered=False,
            sent_by=request.user,
            sent_to=request.user.email,
            error=str(error)
            )
        except (MultiValueDictKeyError, MultiPartParserError, KeyError), error:
            # 400 catch
            LOGGER.error('Bad Request %s', str(error))
            message = "Bad Request %s" % str(error.args)
            content_type = ''
            if 'application/json' in request.environ['HTTP_ACCEPT']:
                message = json.dumps({'error': error.args[0]})
                content_type = 'application/json'
                status = 400
        finally:
            return message, status, content_type #pylint: disable=lost-exception

    @XBlock.json_handler
    def increment_count(self, data, suffix=''):
        """
        An example handler, which increments the data.
        """
        # Just to show data coming in...
        assert data['hello'] == 'world'

        self.count += 1
        return {"count": self.count}

    # workbench while developing your XBlock.
    @staticmethod
    def workbench_scenarios():
        """A canned scenario for display in the workbench."""
        return [
            ("StaffUngradedAssignmentXBlock",
             """<staffungradedassignment/>
             """),
            ("Multiple StaffUngradedAssignmentXBlock",
             """<vertical_demo>
                <staffungradedassignment/>
                <staffungradedassignment/>
                <staffungradedassignment/>
                </vertical_demo>
             """),
        ]

def _get_sha1(file_descriptor):
    """
    Get file hex digest (fingerprint).
    """
    sha1 = hashlib.sha1()
    for block in iter(partial(file_descriptor.read, BLOCK_SIZE), ''):
        sha1.update(block)
    file_descriptor.seek(0)
    return sha1.hexdigest()

def _now():
    """
    Get current date and time.
    """
    return datetime.datetime.utcnow().replace(tzinfo=pytz.utc)

def load_resource(resource_path):  # pragma: NO COVER
    """
    Gets the content of a resource
    """
    resource_content = pkg_resources.resource_string(__name__, resource_path)
    return unicode(resource_content)

def render_template(template_path, context=None):  # pragma: NO COVER
    """
    Evaluate a template by resource path, applying the provided context.
    """
    if context is None:
        context = {}

    template_str = load_resource(template_path)
    template = Template(template_str)
    return template.render(Context(context))

def require(assertion):
    """
    Raises PermissionDenied if assertion is not true.
    """
    if not assertion:
        raise PermissionDenied
