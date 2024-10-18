document.getElementById('contact-form').onsubmit = function(event) {
  event.preventDefault(); // Prevent the form from submitting

  var xhr = new XMLHttpRequest();
  var url = '/submit-contact'; // The endpoint where the Flask app is expecting POST requests

  xhr.open("POST", url, true);
  xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded");

  xhr.onreadystatechange = function() {
      if (xhr.readyState === XMLHttpRequest.DONE && xhr.status === 200) {
          alert('Thank you for your message!');
      }
  };

  var formData = new FormData(document.getElementById('contact-form'));
  var encodedData = new URLSearchParams(formData).toString();

  xhr.send(encodedData);
};
