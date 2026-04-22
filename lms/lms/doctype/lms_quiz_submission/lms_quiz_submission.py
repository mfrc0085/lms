# Copyright (c) 2021, FOSS United and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.model.document import Document
from frappe.utils import cint


class LMSQuizSubmission(Document):
    def validate(self):
        self.validate_if_max_attempts_exceeded()
        self.validate_marks()
        self.set_percentage()

    def on_update(self):
        self.notify_member()
        self.generate_certificate_if_passed()

    def validate_if_max_attempts_exceeded(self):
        max_attempts = frappe.db.get_value("LMS Quiz", self.quiz, ["max_attempts"])
        if max_attempts == 0:
            return

        current_user_submission_count = frappe.db.count(
            self.doctype, filters={"quiz": self.quiz, "member": frappe.session.user}
        )
        if current_user_submission_count >= max_attempts:
            frappe.throw(
                _("You have exceeded the maximum number of attempts ({0}) for this quiz").format(
                    max_attempts
                ),
                MaximumAttemptsExceededError,
            )

    def validate_marks(self):
        self.score = 0
        for row in self.result:
            if cint(row.marks) > cint(row.marks_out_of):
                frappe.throw(
                    _(
                        "Marks for question number {0} cannot be greater than the marks allotted for that question."
                    ).format(row.idx)
                )
            else:
                self.score += cint(row.marks)

    def set_percentage(self):
        if self.score and self.score_out_of:
            self.percentage = (self.score / self.score_out_of) * 100

    def notify_member(self):
        if self.score != 0 and self.has_value_changed("score"):
            notification = frappe._dict(
                {
                    "subject": _("You have got a score of {0} for the quiz {1}").format(
                        (frappe.bold(self.score)), frappe.bold(self.quiz_title)
                    ),
                    "email_content": _(
                        "There has been an update on your submission. You have got a score of {0} for the quiz {1}"
                    ).format(frappe.bold(self.score), frappe.bold(self.quiz_title)),
                    "document_type": self.doctype,
                    "document_name": self.name,
                    "for_user": self.member,
                    "from_user": frappe.session.user,
                    "type": "Alert",
                    "link": "",
                }
            )

            make_notification_logs(notification, [self.member])

    # --- NUEVA FUNCIONALIDAD: Generación Automática de Certificados ---
    def generate_certificate_if_passed(self):
        # 1. Verificar si aprobó
        if not self.percentage or self.percentage < self.passing_percentage:
            return # No aprobó: El sistema ya muestra la nota. Silencio = OK.

        # 2. Verificar si el curso existe y tiene certificación
        if not self.course:
            return # Error de datos, mejor silencio para no confundir.
            
        course_doc = frappe.get_doc("LMS Course", self.course)
        
        # Si el curso NO tiene certificación habilitada, no decimos nada (principio de no molestar).
        if not course_doc.enable_certification:
            return

        # 3. Gestión de Inscripción (Con feedback al usuario)
        is_enrolled = frappe.db.exists("LMS Enrollment", {"member": self.member, "course": self.course})
        
        if not is_enrolled:
            # CASO A: Curso GRATUITO -> Inscribir automáticamente y avisar
            if not course_doc.paid_course:
                try:
                    enrollment = frappe.new_doc("LMS Enrollment")
                    enrollment.member = self.member
                    enrollment.course = self.course
                    enrollment.member_type = "Student"
                    enrollment.insert(ignore_permissions=True)
                    # Feedback claro para el usuario
                    frappe.msgprint(_("Has sido inscrito automáticamente en el curso."))
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "Auto-enrollment failed")
                    frappe.msgprint(_("Ocurrió un error al inscribirte. Contacta al administrador."))
                    return
            else:
                # CASO B: Curso DE PAGO -> No inscribir, avisar qué hacer
                frappe.msgprint(
                    _("Has aprobado el quiz, pero este curso requiere inscripción de pago para emitir el certificado.")
                )
                return # Detenemos el proceso aquí.

        # 4. Verificar si ya tiene certificado
        if frappe.db.exists("LMS Certificate", {"member": self.member, "course": self.course}):
            # Ya tiene certificado, no es un error, es informativo.
            frappe.msgprint(_("Ya tienes un certificado para este curso."))
            return 

        # 5. Buscar/Crear Plantilla
        template_name = frappe.db.get_value("Print Format", {"name": "Standard Certificate"})
        if not template_name:
            template_name = self.create_default_certificate_template()

        # 6. Crear el Certificado
        try:
            cert = frappe.new_doc("LMS Certificate")
            cert.member = self.member
            cert.member_name = self.member_name
            cert.course = self.course
            cert.course_title = course_doc.title
            cert.issue_date = frappe.utils.today()
            cert.template = template_name
            cert.published = 1
            cert.insert(ignore_permissions=True)
            
            frappe.msgprint(_("¡Felicidades! Has obtenido el certificado para este curso."))
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Error generating certificate")
            # Error técnico: El usuario necesita saber que algo falló
            frappe.msgprint(_("Hubo un error al generar tu certificado. Por favor contacta al soporte."))

    def create_default_certificate_template(self):
        """Crea un formato de impresión básico si no existe ninguno."""
        if frappe.db.exists("Print Format", "Standard Certificate"):
            return "Standard Certificate"

        print_format = frappe.new_doc("Print Format")
        print_format.name = "Standard Certificate"
        print_format.doc_type = "LMS Certificate"
        print_format.print_format_type = "Jinja"
        print_format.standard = "No"
        
        print_format.html = """
        <div style="text-align: center; padding: 50px; font-family: Arial, sans-serif; border: 10px solid #333;">
            <h1 style="font-size: 36px; color: #555;">CERTIFICADO DE FINALIZACIÓN</h1>
            <br><br>
            <p style="font-size: 18px;">Se certifica que</p>
            <h2 style="font-size: 28px; color: #000;">{{ doc.member_name }}</h2>
            <p style="font-size: 18px;">ha completado satisfactoriamente el curso</p>
            <h2 style="font-size: 28px; color: #000;">{{ doc.course_title }}</h2>
            <br><br>
            <p style="font-size: 14px; color: #777;">Fecha de emisión: {{ doc.issue_date }}</p>
        </div>
        """
        print_format.save(ignore_permissions=True)
        return print_format.name


class MaximumAttemptsExceededError(frappe.DuplicateEntryError):
    pass